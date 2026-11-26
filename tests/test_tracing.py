"""Langfuse 연결 시험: 기본은 꺼짐, 켜는 조건, 데이터가 컴퓨터 밖으로 나가지 않게 하는 호스트 검사, 실패가 답을 막지 않음. Langfuse 서버는 쓰지 않는다."""
import os
import sys
from pathlib import Path

import pytest
from langchain_core.callbacks import BaseCallbackHandler

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_graph as tg  # noqa: E402
from app import tracing  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL", "LANGFUSE_TRACING", "LANGFUSE_ALLOW_REMOTE")


class Recorder(BaseCallbackHandler):
    def __init__(self):
        self.started = []

    def on_chain_start(self, serialized, inputs, **kw):
        self.started.append(kw.get("name") or (serialized or {}).get("name"))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    tracing._warned.clear()
    made = []
    monkeypatch.setattr(tracing, "_make_handler", lambda: made.append(1) or Recorder())
    yield made
    for k in ENV:                                                  # langfuse_config가 채운 LANGFUSE_HOST가 다른 시험에 남지 않게
        os.environ.pop(k, None)


def on(monkeypatch, host=None, **extra):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    if host:
        monkeypatch.setenv("LANGFUSE_HOST", host)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_it_is_off_by_default_and_builds_no_handler(clean):
    assert tracing.langfuse_config("s1") == {} and not clean


def test_one_key_alone_does_not_turn_it_on(monkeypatch, clean):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    assert tracing.langfuse_config("s1") == {} and not clean


def test_the_off_switch_wins_over_the_keys(monkeypatch, clean):
    on(monkeypatch, LANGFUSE_TRACING="0")
    assert tracing.langfuse_config("s1") == {} and not clean


@pytest.mark.parametrize("host", ["http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000", "http://host.docker.internal:3000", "http://langfuse-web:3000"])
def test_local_hosts_are_allowed(monkeypatch, host):
    on(monkeypatch, host)
    cfg = tracing.langfuse_config("sess-1")
    assert len(cfg["callbacks"]) == 1 and cfg["metadata"]["langfuse_session_id"] == "sess-1"


def test_an_unset_host_is_filled_with_the_local_address_because_the_sdk_default_is_the_cloud(monkeypatch):
    on(monkeypatch)
    assert tracing.langfuse_config("s1")["callbacks"] and os.environ["LANGFUSE_HOST"] == "http://localhost:3000"


def test_a_remote_base_url_is_refused_even_when_the_host_looks_local(monkeypatch, clean):
    on(monkeypatch, "http://localhost:3000", LANGFUSE_BASE_URL="https://cloud.langfuse.com")
    assert tracing.langfuse_config("s1") == {} and not clean


@pytest.mark.parametrize("host", ["https://cloud.langfuse.com", "https://us.cloud.langfuse.com", "http://203.0.113.5:3000", "http://localhost.evil.com"])
def test_a_remote_host_is_refused_so_no_data_leaves_the_machine(monkeypatch, clean, host, capsys):
    on(monkeypatch, host)
    assert tracing.langfuse_config("s1") == {} and not clean
    assert "LANGFUSE_ALLOW_REMOTE" in capsys.readouterr().err


def test_a_remote_host_needs_the_explicit_switch(monkeypatch):
    on(monkeypatch, "https://cloud.langfuse.com", LANGFUSE_ALLOW_REMOTE="1")
    assert tracing.langfuse_config("s1")["callbacks"]


def test_a_handler_that_cannot_be_built_does_not_break_answers(monkeypatch, capsys):
    on(monkeypatch)
    monkeypatch.setattr(tracing, "_make_handler", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tracing.langfuse_config("s1") == {} and "boom" in capsys.readouterr().err


def test_when_on_the_graph_run_reaches_the_callback_and_the_answer_is_unchanged(monkeypatch):
    fake = lambda msgs, n: AIMessage(content="약관을 확인해 주세요.")
    a_off, _ = tg.agent(fake)
    plain = a_off.ask("안녕하세요", "s1")
    on(monkeypatch)
    rec = Recorder()
    monkeypatch.setattr(tracing, "_make_handler", lambda: rec)
    a_on, _ = tg.agent(fake)
    traced = a_on.ask("안녕하세요", "s1")
    assert rec.started and traced["answer"] == plain["answer"] and traced["stop_reason"] == plain["stop_reason"]
