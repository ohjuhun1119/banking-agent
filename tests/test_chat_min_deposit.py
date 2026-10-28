"""채팅에서 최소 가입금액 미만으로 넣는다는 질문에 정해진 문구로 답하는지(app/chat.py) 시험. 모델과 검색은 가짜로 바꾼다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from app import chat
except Exception as e:                                       # 검색 쪽 의존성(Chroma, Kiwi 등)이 없는 환경에서는 건너뛴다
    pytest.skip(f"app.chat을 불러오지 못했습니다: {e}", allow_module_level=True)

PHRASE = "최소 가입금액은 100만원입니다."
EVIDENCE = {"status": "ok", "results": [{"rank": 1, "chunk_id": "x", "text": "가입금액 조항", "distance": 0.3,
                                         "source": {"doc_title": "상품설명서", "article": "가입금액"}}]}


def run(question, monkeypatch, search=None, llm=None):
    calls = {"search": [], "llm": 0}

    def fake_search(q, **kw):
        calls["search"].append(q)
        if search is not None:
            return search(q) if callable(search) else search
        return EVIDENCE

    def fake_llm(messages):
        calls["llm"] += 1
        yield "모델 답변"

    monkeypatch.setattr(chat, "search_terms", fake_search)
    monkeypatch.setattr(chat, "_llm_tokens", llm or fake_llm)
    events = list(chat.answer_events(question))
    text = "".join(d["text"] for name, d in events if name == "token")
    return events, text, calls


def test_small_deposit_gets_the_fixed_notice_without_calling_the_model(monkeypatch):
    events, text, calls = run("10만원 넣으면 이자 얼마야?", monkeypatch)
    assert PHRASE in text and "100,000원" in text
    assert calls["llm"] == 0                                 # 모델을 부르지 않는다
    assert [n for n, _ in events] == ["status", "sources", "token", "done"]
    assert events[-1][1]["model"] is None and events[-1][1]["search_ms"] is None
    assert calls["search"] == ["최소 가입금액"]              # 근거 카드는 '최소 가입금액'으로 찾는다


@pytest.mark.parametrize("question", ["10만 원 예금하면 이자가 얼마예요?", "5천원 가입하면?", "100,000원으로 가입하면 이자는요?", "999,999원 넣으면 이자는?"])
def test_other_phrasings_get_the_notice_too(question, monkeypatch):
    _, text, calls = run(question, monkeypatch)
    assert PHRASE in text and calls["llm"] == 0


def test_notice_is_still_given_when_the_evidence_search_fails(monkeypatch):
    def boom(q):
        raise RuntimeError("검색 실패")
    events, text, _ = run("10만원 넣으면 이자 얼마야?", monkeypatch, search=boom)
    assert PHRASE in text and [n for n, _ in events] == ["status", "token", "done"]       # 근거 카드만 빠진다


def test_notice_is_still_given_when_the_search_exits(monkeypatch):
    def exits(q):
        raise SystemExit("Ollama 연결 실패")                  # vectorstore가 Ollama 문제를 SystemExit로 알린다
    _, text, _ = run("10만원 넣으면 이자 얼마야?", monkeypatch, search=exits)
    assert PHRASE in text


@pytest.mark.parametrize("question", ["100만원 넣으면 이자 얼마야?", "1000만원을 예금하면 이자는?", "이자 10만원 받으려면 얼마 넣어야 해요?",
                                      "중도해지하면 이자 얼마 받아요?", "최소 가입금액이 얼마예요?"])
def test_other_questions_take_the_normal_path(question, monkeypatch):
    events, text, calls = run(question, monkeypatch)
    assert PHRASE not in text and text == "모델 답변" and calls["llm"] == 1
    assert [n for n, _ in events][:2] == ["status", "sources"]
