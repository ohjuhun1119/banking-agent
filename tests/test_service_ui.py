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


def test_the_two_tabs_the_form_labels_and_the_disclaimer_are_present():
    html = PAGE.read_text(encoding="utf-8")
    for needle in ('role="tablist"', "상담", "이자 계산", 'id="chat-form"', 'id="calc-form"', "원금", "신규일", "만기일", "해지일", "기본금리", "계산하기", "보내기", "새 대화", "참고용"):
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
