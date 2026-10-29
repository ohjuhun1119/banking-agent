"""에이전트 API(/api/agent/*) 시험. 모델은 가짜로 바꿔 끼우고, 대화 저장소는 시험마다 새로 만든다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
try:
    from fastapi.testclient import TestClient
    from app import api
    from app import graph as g
except Exception as e:                                       # 검색 쪽 의존성(Chroma, Kiwi 등)이 없는 환경에서는 건너뛴다
    pytest.skip(f"app.api를 불러오지 못했습니다: {e}", allow_module_level=True)

from langchain_core.messages import AIMessage  # noqa: E402

client = TestClient(api.app)
BASE = {"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-01-21"}


class Model:
    def __init__(self, fn):
        self.fn, self.n = fn, 0

    def invoke(self, messages):
        self.n += 1
        return self.fn(messages, self.n)


@pytest.fixture
def fresh(monkeypatch):
    def install(fn):
        monkeypatch.setattr(api, "_agent", g.Agent(model=Model(fn)))
    return install


def ask(**body):
    body.setdefault("question", "질문")
    return client.post("/api/agent/ask", json=body)


def test_ask_returns_answer_trace_and_confirmed(fresh):
    fresh(lambda m, n: AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": BASE, "id": "c1"}]) if n == 1
          else AIMessage(content="세전 이자는 547원입니다."))
    r = ask(question="1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-01-21에 해지하면 이자는?", session_id="t1", today="2026-10-28")
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "세전 이자는 547원입니다." and body["trace"][0]["status"] == "ok" and body["tool_calls"] == 1
    assert body["confirmed"]["principal"] == 10_000_000 and "원금 10,000,000원" in body["confirmed_text"] and body["session_id"] == "t1"


def test_session_continues_and_reset_clears_it(fresh):
    fresh(lambda m, n: AIMessage(content=f"{len(m)}"))                     # 모델에 들어간 메시지 수를 답으로 돌려준다
    first = ask(session_id="a").json()["answer"]
    second = ask(session_id="a").json()["answer"]
    other = ask(session_id="b").json()["answer"]
    assert (first, second, other) == ("2", "4", "2")
    assert client.post("/api/agent/reset", json={"session_id": "a"}).json() == {"session_id": "a", "reset": True}
    assert ask(session_id="a").json()["answer"] == "2"


def test_model_failure_is_a_200_with_error_field(fresh):
    def boom(m, n):
        raise ConnectionError("refused")
    fresh(boom)
    r = ask()
    assert r.status_code == 200 and r.json()["answer"] is None and "ollama" in r.json()["error"].lower() and r.json()["stop_reason"] == "model_error"


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "q", "session_id": "../x"}, {"question": "q", "session_id": ""},
                                  {"question": "q", "today": "내일"}, {"question": "x" * 2001}])
def test_bad_requests_are_rejected(fresh, body):
    fresh(lambda m, n: AIMessage(content="끝"))
    assert client.post("/api/agent/ask", json=body).status_code == 422


def test_impossible_date_is_422(fresh):
    fresh(lambda m, n: AIMessage(content="끝"))
    r = ask(today="2026-13-45")
    assert r.status_code == 422 and "today" in r.json()["detail"]


def test_reset_of_unknown_session_is_fine(fresh):
    fresh(lambda m, n: AIMessage(content="끝"))
    assert client.post("/api/agent/reset", json={"session_id": "never-used"}).status_code == 200


def test_invented_value_is_reported_in_the_trace(fresh):
    fresh(lambda m, n: AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": {**BASE, "base_rate": 3.5}, "id": "c1"}]) if n == 1
          else AIMessage(content="끝"))
    body = ask(question="1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-01-21에 해지하면 이자는?", session_id="t2").json()
    assert body["trace"][0]["dropped"] == {"base_rate": 3.5} and "base_rate" not in body["confirmed"]


def test_handoff_request_is_answered_without_calling_the_model(fresh):
    def must_not_be_called(m, n):
        raise AssertionError("상담원 연결 요청에는 모델을 부르면 안 된다")
    fresh(must_not_be_called)
    body = ask(question="상담원 연결해줘", session_id="h1").json()
    assert body["answer"].startswith("상담 요청을 저장했어요") and "접수번호는" in body["answer"]                 # 저장에 성공했을 때만 접수번호를 말한다
    assert body["stop_reason"] == "handoff_saved" and body["model_calls"] == 0 and body["error"] is None


def test_ask_returns_the_evidence_the_code_attached():
    import json

    from langchain_core.messages import AIMessage
    from app import graph as g

    class Fake:
        n = 0

        def invoke(self, msgs):
            Fake.n += 1
            if Fake.n == 1:
                return AIMessage(content="", tool_calls=[{"name": "calc_interest", "id": "c", "type": "tool_call", "args": {
                    "principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-12-07", "base_rate": 3.5}}])
            return AIMessage(content="해지일 2026-12-07에 1000만원을 해지하면 세전 이자는 273,304원이에요.")

    def search(q, **kw):
        return {"status": "ok", "query": q, "results": [{"rank": 1, "chunk_id": "r", "chunk_type": "중도해지", "text": "본문", "source": {"doc_title": "상품설명서", "page": 4}}]}
    api._agent = g.Agent(model=Fake(), search_fn=search)
    r = client.post("/api/agent/ask", json={"question": "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요? 기본금리는 3.5%예요.", "session_id": "ev"})
    assert r.status_code == 200
    d = r.json()
    assert [c["kind"] for c in d["evidence"]] == ["계산", "약관"] and d["evidence_text"].startswith("근거") and "상품설명서" in d["evidence_text"]
    assert "본문" not in json.dumps(d["evidence"], ensure_ascii=False)
