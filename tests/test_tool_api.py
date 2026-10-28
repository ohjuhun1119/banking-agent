"""도구 호출 API(/api/tools/*) 시험. 모델은 가짜로 바꿔 끼운다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
try:
    from fastapi.testclient import TestClient
    from app.api import app
    from app import tool_calling as tc
except Exception as e:                                       # 검색 쪽 의존성(Chroma, Kiwi 등)이 없는 환경에서는 건너뛴다
    pytest.skip(f"app.api를 불러오지 못했습니다: {e}", allow_module_level=True)

from langchain_core.messages import AIMessage  # noqa: E402

client = TestClient(app)


class Fake:
    def __init__(self, calls, error=None):
        self.calls, self.error = calls, error

    def invoke(self, messages):
        if self.error:
            raise self.error
        return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": "c"} for n, a in self.calls])


def use(monkeypatch, calls, error=None):
    monkeypatch.setattr(tc, "get_model", lambda variant=None: Fake(calls, error))


def test_spec_and_cases():
    spec = client.get("/api/tools/spec?variant=short").json()
    assert spec["tools"] == tc.tool_specs("short") and "{today}" in spec["system_prompt"]
    assert client.get("/api/tools/spec?variant=long").status_code == 422
    cases = client.get("/api/tools/cases").json()
    assert len(cases["cases"]) == 25 and cases["today"] == "2026-10-28"


def test_ask_grades_a_case(monkeypatch):
    use(monkeypatch, [("calc_interest", {"principal": "1000만원", "start_date": "2026-01-01",
                                         "maturity_date": "2027-01-01", "termination_date": "2026-01-21"})])
    q = next(c["question"] for c in client.get("/api/tools/cases").json()["cases"] if c["id"] == "C1")
    out = client.post("/api/tools/ask", json={"question": q, "case_id": "C1"}).json()
    assert out["grade"]["pass"] is False and out["grade"]["errors"][0]["code"] == "unparsable_principal"
    assert "tool_results" not in out


def test_ask_executes_tools(monkeypatch):
    use(monkeypatch, [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01",
                                         "maturity_date": "2027-01-01", "termination_date": "2026-07-20", "base_rate": 3.5})])
    out = client.post("/api/tools/ask", json={"question": "아무 질문", "execute": True}).json()
    assert out["tool_results"][0]["result"]["interest_krw"] == 63013 and "grade" not in out


def test_edited_question_is_not_graded(monkeypatch):
    use(monkeypatch, [])
    out = client.post("/api/tools/ask", json={"question": "고친 질문", "case_id": "C1"}).json()
    assert "grade" not in out


def test_unknown_case_and_model_failure(monkeypatch):
    use(monkeypatch, [])
    assert client.post("/api/tools/ask", json={"question": "q", "case_id": "Z9"}).status_code == 404
    use(monkeypatch, [], error=ConnectionError("refused"))
    r = client.post("/api/tools/ask", json={"question": "q"})
    assert r.status_code == 503 and "ollama" in r.json()["detail"].lower()
