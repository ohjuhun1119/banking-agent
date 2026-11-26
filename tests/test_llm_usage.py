"""API 모델 하루 호출 상한 시험: 돈이 나가는 호출을 코드가 세고, 상한을 넘으면 모델을 부르지 않는다. 서버 재시작에도 유지된다 (SQLite)."""
import json
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_graph as tg  # noqa: E402
from app import graph as g, tool_calling as tc, usage  # noqa: E402

pytest.importorskip("langchain_openai")
ENV = ("LLM_PROVIDER", "LLM_ALLOW_EXTERNAL", "LLM_MODEL", "OPENAI_API_KEY", "LLM_TEMPERATURE", "OPENAI_BASE_URL", "LLM_DAILY_CALL_LIMIT")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def test_reserve_counts_calls_and_refuses_the_one_past_the_limit():
    assert [usage.reserve(3, "2026-11-26")[0] for _ in range(5)] == [True, True, True, False, False]
    assert usage.used("2026-11-26") == 3                                          # 거절된 호출은 세지 않는다


def test_a_new_day_starts_from_zero():
    for _ in range(3):
        usage.reserve(3, "2026-11-26")
    assert usage.reserve(3, "2026-11-27") == (True, 1) and usage.used("2026-11-26") == 3


def test_the_day_is_the_korean_date_not_the_servers_utc_date(monkeypatch):
    """서버(UTC)에서 한국 시간 새벽 0~9시에도 날짜가 한국 기준으로 바뀐다."""
    import datetime as dt

    class Fake(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 26, 16, 30, tzinfo=dt.timezone.utc).astimezone(tz)          # UTC 16:30 = 한국 27일 01:30
    monkeypatch.setattr(usage.dt, "datetime", Fake)
    assert usage.today() == "2026-11-27"


def test_the_count_survives_a_restart():
    usage.reserve(5, "2026-11-26")
    usage.reserve(5, "2026-11-26")
    con = sqlite3.connect(usage.DB_PATH)                                                    # 새 연결 = 프로세스를 다시 띄운 것과 같다
    assert con.execute("select calls from usage where day='2026-11-26'").fetchone() == (2,)


def test_concurrent_calls_never_exceed_the_limit():
    results = []

    def work():
        results.append(usage.reserve(20, "2026-11-26")[0])
    threads = [threading.Thread(target=work) for _ in range(60)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 20 and usage.used("2026-11-26") == 20


@pytest.mark.parametrize("raw,expected", [(None, 300), ("", 300), ("100", 100), ("0", None)])
def test_the_limit_defaults_to_300_and_zero_means_unlimited(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("LLM_DAILY_CALL_LIMIT", raw)
    assert usage.daily_limit() == expected


@pytest.mark.parametrize("raw", ["many", "-5", "1.5"])
def test_an_invalid_limit_is_an_error_not_silently_unlimited(monkeypatch, raw):
    monkeypatch.setenv("LLM_DAILY_CALL_LIMIT", raw)
    with pytest.raises(ValueError, match="LLM_DAILY_CALL_LIMIT"):
        usage.daily_limit()


@pytest.fixture
def fake_openai(monkeypatch):
    bodies = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            out = json.dumps({"id": "c", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "약관을 확인했어요."}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k, v in {"LLM_PROVIDER": "openai", "LLM_ALLOW_EXTERNAL": "1", "OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": f"http://127.0.0.1:{srv.server_port}/v1"}.items():
        monkeypatch.setenv(k, v)
    yield bodies
    srv.shutdown()


def test_past_the_limit_the_model_is_not_called_and_the_user_hears_why(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_CALL_LIMIT", "1")
    res = g.Agent(search_fn=tg.search_ok).ask("정기예금 중도해지하면 이자는 어떻게 돼요?", "b1")             # 질문 하나는 모델 호출 2번이 필요하다
    assert len(fake_openai) == 1 and usage.used() == 1                                      # 두 번째는 API로 나가지 않았다
    assert res["answer"] is None and "한도" in res["error"] and "1회" in res["error"] and "OPENAI_API_KEY" not in res["error"]


def test_the_budget_runs_out_across_questions_and_the_next_day_resets(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_CALL_LIMIT", "2")
    a = g.Agent(search_fn=tg.search_ok)
    assert a.ask("정기예금 중도해지하면 이자는 어떻게 돼요?", "b2")["error"] is None
    assert "한도" in a.ask("정기예금 중도해지하면 이자는 어떻게 돼요?", "b3")["error"] and len(fake_openai) == 2
    monkeypatch.setattr(usage, "today", lambda: "2099-01-01")
    assert a.ask("정기예금 중도해지하면 이자는 어떻게 돼요?", "b4")["error"] is None


def test_local_mode_never_counts_and_never_creates_the_usage_file():
    model = tc.get_model()
    assert type(model.bound).__name__ == "ChatOllama" and not usage.DB_PATH.exists()


def test_health_shows_todays_usage_only_in_api_mode(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from app import api
    client = TestClient(api.app)
    monkeypatch.delenv("API_KEY", raising=False)
    assert "llm_calls_today" not in client.get("/health").json()
    for k, v in {"LLM_PROVIDER": "openai", "LLM_ALLOW_EXTERNAL": "1", "OPENAI_API_KEY": "sk-test", "LLM_DAILY_CALL_LIMIT": "100"}.items():
        monkeypatch.setenv(k, v)
    usage.reserve(100)
    h = client.get("/health").json()
    assert h["llm_calls_today"] == 1 and h["llm_daily_limit"] == 100
