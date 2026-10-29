"""공개 엔드포인트 /chat · /health 시험 (9일차). 모델은 가짜로 바꿔 끼운다."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
try:
    from fastapi.testclient import TestClient
    from app import api, traces
    from app import graph as g
except Exception as e:                                       # 검색 쪽 의존성이 없는 환경에서는 건너뛴다
    pytest.skip(f"app.api를 불러오지 못했습니다: {e}", allow_module_level=True)

from langchain_core.messages import AIMessage  # noqa: E402

client = TestClient(api.app)
RULE_Q = "예금 만기일이 지나도 안 찾았는데 지금 해지하면 이자는 어떻게 계산돼요?"          # 검증용 H14
CALC_Q = "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요? 기본금리는 3.5%예요."
CALC = {"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-12-07", "base_rate": 3.5}


class Model:
    def __init__(self, fn):
        self.fn, self.n = fn, 0

    def invoke(self, messages):
        self.n += 1
        return self.fn(messages, self.n)


def search(q, **kw):
    return {"status": "ok", "query": q, "results": [{"rank": 1, "chunk_id": "r", "chunk_type": "중도해지", "text": "본문 전체",
                                                    "source": {"doc_title": "상품설명서", "page": 4}}]}


@pytest.fixture
def fresh(monkeypatch):
    def install(fn):
        monkeypatch.setattr(api, "_agent", g.Agent(model=Model(fn), search_fn=search))
    return install


def chat(**body):
    body.setdefault("question", "질문")
    return client.post("/chat", json=body)


def calc_then_answer(m, n):
    return (AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": CALC, "id": "c1"}]) if n == 1
            else AIMessage(content="해지일 2026-12-07에 1000만원을 해지하면 세전 이자는 273,304원이에요."))


# ---------- 응답 모양 ----------
def test_chat_returns_session_answer_tools_evidence_and_timing(fresh):
    fresh(calc_then_answer)
    r = chat(question=CALC_Q, session_id="u1", today="2026-10-28")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session_id", "answer", "error", "stop_reason", "tools", "evidence", "evidence_text", "tool_calls", "model_calls", "ms", "trace_id"}
    assert body["session_id"] == "u1" and body["answer"].endswith("273,304원이에요.") and body["error"] is None and body["stop_reason"] is None
    assert body["tools"] == [{"name": "calc_interest", "status": "ok", "forced": None, "retried": False, "dropped": [], "filled": [], "ms": body["tools"][0]["ms"]}]
    assert [c["kind"] for c in body["evidence"]] == ["계산", "약관"] and body["evidence_text"].startswith("근거")
    assert body["tool_calls"] == 1 and body["model_calls"] == 2 and body["ms"] >= 0 and isinstance(body["trace_id"], int)


def test_chat_never_returns_raw_arguments_or_document_text(fresh):
    fresh(lambda m, n: AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": {**CALC, "principal": 20_000_000}, "id": "c1"}]) if n == 1
          else AIMessage(content="원금과 신규일을 알려주세요."))
    text = json.dumps(chat(question=CALC_Q, today="2026-10-28").json(), ensure_ascii=False)
    assert "20000000" not in text and "20,000,000" not in text and "본문 전체" not in text           # 지어낸 값 · 약관 본문이 응답에 없다
    body = json.loads(text)
    assert body["tools"][0]["dropped"] == ["principal"]                                              # 칸 이름만


def test_forced_calls_and_handoff_are_reported_in_the_tool_list(fresh):
    fresh(lambda m, n: AIMessage(content="약관상 가능해요."))
    body = chat(question="1,200만 원 예금 중에 300만 원만 먼저 뺄 수 있어요?").json()
    assert body["tools"][0]["name"] == "search_terms" and body["tools"][0]["forced"] == "search"
    saved = chat(question="상담원 연결해줘", session_id="h").json()
    assert saved["stop_reason"] == "handoff_saved" and saved["tools"][0]["name"] == "request_handoff" and saved["tools"][0]["forced"] == "handoff" and saved["model_calls"] == 0


# ---------- 세션 ----------
def test_the_same_session_continues_and_delete_resets_it(fresh):
    fresh(lambda m, n: AIMessage(content="답"))
    chat(question="첫 질문", session_id="a")
    chat(question="둘째 질문", session_id="a")
    chat(question="다른 세션", session_id="b")
    agent = api.get_agent()
    assert len(agent.history("a")) == 4 and len(agent.history("b")) == 2                                # 세션별로 따로 쌓인다
    r = client.delete("/chat/a")
    assert r.status_code == 200 and r.json() == {"session_id": "a", "reset": True} and agent.history("a") == [] and len(agent.history("b")) == 2


# ---------- 오류 ----------
def test_model_failure_is_a_503_with_the_reason_and_a_trace(fresh):
    def down(m, n):
        raise RuntimeError("ollama off")
    fresh(down)
    r = chat(question="질문")
    body = r.json()
    assert r.status_code == 503 and body["answer"] is None and "모델 호출 실패" in body["error"] and body["stop_reason"] == "model_error"
    (row,) = traces.list_traces()
    assert "model_error" in row["signals"] and "error" in row["signals"]


@pytest.mark.parametrize("body", [
    {"question": ""}, {"question": "   "}, {"question": "가" * 2001}, {}, {"question": "q", "session_id": "bad id"}, {"question": "q", "session_id": ""},
    {"question": "q", "session_id": "x" * 65}, {"question": "q", "today": "2026-13-45"}, {"question": "q", "today": "내일"},
])
def test_invalid_requests_are_422(fresh, body):
    fresh(lambda m, n: AIMessage(content="답"))
    assert client.post("/chat", json=body).status_code == 422
    assert traces.list_traces() == []                                                                    # 잘못된 요청은 기록하지 않는다


def test_delete_with_a_bad_session_id_is_422(fresh):
    fresh(lambda m, n: AIMessage(content="답"))
    assert client.delete("/chat/bad id!").status_code == 422


# ---------- 트레이스 ----------
def test_every_chat_turn_is_traced_and_the_wrong_tool_pattern_is_flagged_end_to_end(fresh):
    fresh(lambda m, n: AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": {"maturity_date": "2026-10-31", "termination_date": "2026-11-28",
                                                                                         "principal": 10_000_000, "start_date": "2026-01-01"}, "id": "c1"}]) if n == 1
          else AIMessage(content="계산을 위해서는 원금, 신규일, 만기일이 필요합니다. 이 값을 알려주세요."))
    body = chat(question=RULE_Q, session_id="t", today="2026-10-28").json()
    (row,) = traces.list_traces()
    assert body["trace_id"] == row["id"] and row["question"] == RULE_Q and row["session_id"] == "t"
    assert "calc_on_rule_question" in row["signals"] and row["tools"][0]["args"]["principal"] == 10_000_000             # 인자 원본은 트레이스에 남는다
    assert traces.mark(row["id"], "wrong_tool", "H14 유형") and "H14 유형" in traces.export_markdown("wrong_tool")


def test_the_screen_endpoint_is_traced_too(fresh):
    fresh(lambda m, n: AIMessage(content="답"))
    body = client.post("/api/agent/ask", json={"question": "화면에서 한 질문", "session_id": "web"}).json()
    assert isinstance(body["trace_id"], int) and traces.list_traces()[0]["question"] == "화면에서 한 질문"


def test_a_broken_trace_store_never_breaks_the_answer(fresh, tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    monkeypatch.setattr(traces, "DB_PATH", folder)
    fresh(lambda m, n: AIMessage(content="답"))
    r = chat(question="질문")
    assert r.status_code == 200 and r.json()["answer"] == "답" and r.json()["trace_id"] is None


def test_tracing_can_be_switched_off(fresh, monkeypatch):
    monkeypatch.setenv("TRACE_LOG", "0")
    fresh(lambda m, n: AIMessage(content="답"))
    assert chat(question="질문").json()["trace_id"] is None and traces.list_traces() == []
    assert client.get("/health").json()["trace_log"] is False


# ---------- 상태 확인 · 문서 ----------
def test_health_and_openapi(fresh):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["model"] and body["trace_log"] is True
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/chat"] and "delete" in paths["/chat/{session_id}"] and "get" in paths["/health"]
    schema = client.get("/openapi.json").json()["components"]["schemas"]["PublicChatRequest"]
    assert schema["properties"]["question"]["maxLength"] == 2000 and schema["required"] == ["question"]
    assert not [k for k in client.get("/openapi.json").json()["components"]["schemas"] if k.startswith("app__")]        # 이름이 겹치는 모델이 없다


def test_the_langsmith_tags_and_metadata_are_passed_to_the_graph(fresh, monkeypatch):
    seen = {}
    fresh(lambda m, n: AIMessage(content="답"))
    graph = api.get_agent().graph
    real = graph.invoke

    def spy(inp, cfg=None, **kw):
        seen.update(cfg or {})
        return real(inp, cfg, **kw)
    monkeypatch.setattr(type(graph), "invoke", lambda self, inp, cfg=None, **kw: spy(inp, cfg, **kw), raising=False)
    chat(question="질문", session_id="meta")
    assert seen["tags"] == ["banking-agent"] and seen["metadata"] == {"session_id": "meta"} and seen["run_name"] == "banking-agent-turn"
