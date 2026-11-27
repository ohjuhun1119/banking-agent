"""서비스 화면(상담 · 이자 계산) 시험: '/'는 새 화면, '/debug'는 기존 개발용 확인 화면, 개발 용어 노출 없음, 서버가 준 글을 HTML로 넣지 않음(XSS), 장식 없음."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app import api  # noqa: E402

STATIC = ROOT / "app" / "static"
PAGE, CSS, JS = (STATIC / n for n in ("service.html", "service.css", "service.js"))
client = TestClient(api.app, follow_redirects=False)


def test_the_root_serves_the_service_screen_and_debug_keeps_the_old_check_screen(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    home, debug = client.get("/"), client.get("/debug")
    assert home.status_code == 200 and "<title>정기예금 상담</title>" in home.text and "/static/service.css" in home.text and "/static/service.js" in home.text
    assert debug.status_code == 200 and "확인 화면" in debug.text and "확인 화면" not in home.text


def test_debug_is_behind_the_access_key_and_a_browser_without_it_is_sent_to_the_login_page(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    for path in ("/", "/debug"):
        res = client.get(path)
        assert res.status_code == 303 and res.headers["location"] == "/login", path
    assert client.get("/debug", cookies={"api_key": "secret-1"}).status_code == 200


def test_the_screen_files_are_served_to_a_logged_in_browser(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    for name in ("service.css", "service.js"):
        assert client.get(f"/static/{name}", cookies={"api_key": "secret-1"}).status_code == 200
        assert client.get(f"/static/{name}").status_code == 401


def test_no_developer_vocabulary_reaches_the_user():
    text = " ".join(p.read_text(encoding="utf-8") for p in (PAGE, JS))
    for word in ("search_terms", "calc_interest", "tool_calls", "model_calls", "stop_reason", "trace", "Agent", "에이전트", "LLM", "확인 화면", "도구", "토큰", "프롬프트"):
        assert word not in text, word


def test_the_script_never_inserts_server_text_as_html():
    js = JS.read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", js)         # 답변 · 근거는 textContent로만 넣는다


def test_the_script_uses_the_existing_endpoints_and_the_session_is_per_tab():
    js = JS.read_text(encoding="utf-8")
    for needle in ('"/chat"', '"/api/calc"', "sessionStorage", "DELETE", "/login"):
        assert needle in js, needle


def test_the_screen_is_undecorated_and_self_contained():
    css, page, js = (p.read_text(encoding="utf-8") for p in (CSS, PAGE, JS))
    assert not re.search(r"gradient|box-shadow|text-shadow|animation|transition|backdrop|filter|@import|url\(", css)
    assert not re.search(r"https?://|<img|<svg|@import", page + js)
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", css + page + js)                           # 이모지 없음


def test_the_four_menu_tabs_the_form_labels_and_the_disclaimer_are_present():
    html = PAGE.read_text(encoding="utf-8")
    labels = re.findall(r'<button[^>]*role="tab"[^>]*>([^<]+)</button>', html)
    assert labels == ["메인", "상담 문의", "상담 이력", "이자 계산", "상담원 연결"], labels
    for needle in ('role="tablist"', 'id="chat-form"', 'id="calc-form"', "원금", "신규일", "만기일", "해지일", "기본금리", "계산하기", "보내기", "새 문의", "참고용"):
        assert needle in html, needle
    assert html.count('type="date"') == 3


# ---------- 채팅 화면 다듬기: 자주 묻는 질문 버튼 · 경과 시간 ----------
def faq_questions():
    html = PAGE.read_text(encoding="utf-8")
    return re.findall(r'<button[^>]*class="faq-item"[^>]*data-q="([^"]+)"', html)


def test_the_first_screen_offers_a_few_frequently_asked_questions_as_buttons():
    qs = faq_questions()
    assert 3 <= len(qs) <= 4 and len(set(qs)) == len(qs) and all(len(q) <= 30 for q in qs), qs
    assert "무엇이 궁금하세요?" in PAGE.read_text(encoding="utf-8")


def test_every_suggested_question_passes_the_scope_gate_so_a_tap_never_ends_in_a_refusal():
    from app.graph import out_of_scope
    from app.scope import off_topic
    for q in faq_questions():
        assert not out_of_scope(q) and not off_topic(q), q


def test_tapping_a_suggestion_sends_it_and_a_new_conversation_brings_the_buttons_back():
    js = JS.read_text(encoding="utf-8")
    assert "faq-item" in js and "data-q" in js and "requestSubmit" in js
    assert "faq.remove()" in js and "messages.appendChild(faq)" in js                      # 첫 질문 뒤에는 사라지고 '새 대화'에서 돌아온다


def test_the_wait_message_counts_the_elapsed_seconds_without_animation():
    js = JS.read_text(encoding="utf-8")
    assert "setInterval" in js and "clearInterval" in js and "초)" in js


# ---------- 메뉴 4개: 메인(서비스 소개) · 상담 문의 · 이자 계산 · 상담원 연결 ----------
def panel(html: str, pid: str) -> str:
    m = re.search(r'<section id="%s".*?</section>' % pid, html, re.S)
    assert m, pid
    return m.group(0)


def test_the_main_tab_is_the_one_open_on_arrival_and_the_others_start_hidden():
    html = PAGE.read_text(encoding="utf-8")
    assert re.search(r'id="tab-home"[^>]*aria-selected="true"', html)
    for tab in ("chat", "history", "calc", "handoff"):
        assert re.search(r'id="tab-%s"[^>]*aria-selected="false"' % tab, html), tab
        assert re.search(r'<section id="panel-%s"[^>]*\bhidden\b' % tab, html), tab
    assert not re.search(r'<section id="panel-home"[^>]*\bhidden\b', html)


def test_the_main_screen_says_what_the_service_does_and_links_to_the_three_features():
    home = panel(PAGE.read_text(encoding="utf-8"), "panel-home")
    text = re.sub(r"<[^>]+>", " ", home)
    for needle in ("하나의 정기예금", "중도해지", "이자", "약관", "세전", "참고용"):
        assert needle in text + PAGE.read_text(encoding="utf-8"), needle
    targets = re.findall(r'data-go="(tab-[a-z]+)"', home)
    assert targets == ["tab-calc", "tab-chat", "tab-handoff"], targets
    assert "세후" in text and "계산하지 않" in text                                          # 못 하는 것도 미리 알린다 (세후 금액)


def test_the_handoff_screen_is_honest_that_it_only_leaves_a_request_and_asks_before_saving():
    box = panel(PAGE.read_text(encoding="utf-8"), "panel-handoff")
    text = re.sub(r"<[^>]+>", " ", box)
    assert "접수번호" in text and "실시간" in text and "연락" in text and "개인정보" in text      # 연결도 연락도 없다는 것, 개인정보를 적지 말라는 것
    assert re.search(r'<textarea[^>]*id="handoff-text"[^>]*maxlength="150"', box)
    for ident in ("handoff-start", "handoff-confirm", "handoff-save", "handoff-cancel", "handoff-result"):
        assert 'id="%s"' % ident in box, ident
    assert re.search(r'id="handoff-confirm"[^>]*\bhidden\b', box)                           # 저장 확인은 누르기 전까지 숨겨져 있다


def handoff_prefix():
    m = re.search(r'HANDOFF_PREFIX = "([^"]+)"', JS.read_text(encoding="utf-8"))
    assert m
    return m.group(1)


def test_the_sentence_the_screen_sends_is_recognized_as_a_handoff_request_even_with_a_short_note():
    """문의 내용이 짧아도(예: '재가입') 서버가 '짧은 상담원 연결 요청'(20자 이하)으로 보고 내용을 버리면 '저장할 질문이 없어요'가 된다."""
    from app.answer_check import handoff_request
    prefix = handoff_prefix()
    assert len(prefix) > 20
    for note in ("재가입", "중도해지 후 재가입 조건이 궁금해요", "가" * 150):
        question = prefix + note
        assert handoff_request(question) and len(question) <= 2000


def test_the_handoff_screen_saves_the_note_and_returns_a_ticket_without_calling_the_model(monkeypatch):
    from app.tools import handoff as hf
    monkeypatch.delenv("API_KEY", raising=False)
    res = client.post("/chat", json={"question": handoff_prefix() + "재가입 조건이 궁금해요", "session_id": "nav-1"})
    body = res.json()
    assert res.status_code == 200 and body["model_calls"] == 0 and "접수번호" in body["answer"] and "연락드리거나 연결해 주지는 않아요" in body["answer"]
    assert hf.count_requests() == 1


def test_the_script_guards_an_empty_note_and_confirms_before_saving():
    js = JS.read_text(encoding="utf-8")
    assert "상담받고 싶은 내용을 입력해 주세요" in js and "handoff-confirm" in js and "handoff-save" in js
    assert js.index("handoff-confirm") < js.index("handoff-save")                              # 저장 전에 확인 단계를 먼저 거친다
    assert "data-go" in js                                                                      # 메인의 항목을 누르면 그 메뉴로 간다


def test_past_conversations_have_their_own_tab_next_to_chat_and_stay_in_this_browser():
    html = PAGE.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert html.index('id="tab-chat"') < html.index('id="tab-history"') < html.index('id="tab-calc"')
    hist = panel(html, "panel-history")
    for needle in ('id="history-list"', 'id="history-clear"', "이 기기에만"):
        assert needle in hist, needle
    assert 'id="history-open"' not in html and 'id="history-box"' not in html          # 문의 화면 안의 버튼은 없어졌다
    assert "localStorage" in js and "chat_history" in js and "innerHTML" not in js
    assert "지난 문의가 없어요" in js and 'tab-history' in js


def test_the_screen_tells_visitors_that_questions_are_stored_and_not_to_enter_personal_data():
    html = PAGE.read_text(encoding="utf-8")
    for pid in ("panel-home", "panel-chat"):
        text = re.sub(r"<[^>]+>", " ", panel(html, pid))
        assert "30일" in text and "저장" in text and "개인정보" in text, pid


def test_every_saved_request_is_listed_on_the_handoff_tab_whether_it_came_from_the_form_or_from_the_chat():
    html = PAGE.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    hand = panel(html, "panel-handoff")
    assert 'id="handoff-log"' in hand and "요청 내역" in hand and "이 기기에만" in hand
    assert "handoff_requests" in js and "접수번호는" in js
    form_part = js[js.index('$("handoff-save")'):]
    chat_part = js[js.index('$("chat-form")'):js.index('Array.prototype.forEach.call(document.querySelectorAll(".faq-item")')]
    assert "rememberRequest(" in form_part and "rememberRequest(" in chat_part            # 두 길 모두 같은 내역에 남긴다
    assert 'tab-handoff' in js[js.index("function selectTab"):js.index("function selectTab") + 600]      # 탭을 열 때마다 목록을 다시 그린다


def test_each_clause_in_the_evidence_opens_to_show_its_full_text_loaded_on_demand():
    js = JS.read_text(encoding="utf-8")
    body = js[js.index("function evidence("):js.index("var messages = $(")]
    assert "/api/chunks/" in body and "encodeURIComponent" in body and '"toggle"' in body        # 열 때 한 번 조항 본문을 불러온다
    assert "d.evidence)" in js or "d.evidence," in js                                           # 근거 카드를 받아서 그린다
    assert "근거 약관" in body and "innerHTML" not in js


def test_the_clause_text_the_page_asks_for_is_served_for_a_card_id_from_the_evidence(monkeypatch, tmp_path):
    """약관 파일은 저장소에 없으므로(비공개) 임시 파일로 시험한다. 없는 ID는 404다."""
    import json
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps({"chunk_id": "doc__art7", "text": "제7조 이자계산 이 예금의 이자 계산은 일단위로 합니다.", "metadata": {}}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(api, "CHUNKS", chunks)
    monkeypatch.delenv("API_KEY", raising=False)
    client = TestClient(api.app)
    res = client.get("/api/chunks/doc__art7")
    assert res.status_code == 200 and "이자" in res.json()["text"]
    assert client.get("/api/chunks/missing").status_code == 404


def test_the_request_list_can_be_cleared_from_the_handoff_tab():
    html = PAGE.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert 'id="handoff-clear"' in panel(html, "panel-handoff") and "기록 지우기" in panel(html, "panel-handoff")
    body = js[js.index('$("handoff-clear")'):]
    assert "removeItem(REQUEST_KEY)" in body[:300] and "renderHandoffLog()" in body[:300]


def test_both_clear_buttons_sit_at_the_top_right_of_their_box_in_bold():
    html = PAGE.read_text(encoding="utf-8")
    css = (PAGE.parent / "service.css").read_text(encoding="utf-8")
    for pid, bid in (("panel-history", "history-clear"), ("panel-handoff", "handoff-clear")):
        box = panel(html, pid)
        assert f'id="{bid}" class="clear"' in box
        inner = box[box.index('<div class="history">'):]
        assert inner.index(bid) < inner.index("help")                         # 박스의 맨 앞에 두어 오른쪽 위에 뜬다
    rule = css[css.index(".clear{"):].split("}")[0]
    assert "float:right" in rule and "font-weight:700" in rule
