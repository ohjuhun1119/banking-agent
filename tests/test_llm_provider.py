"""모델 제공사 선택 시험: 기본은 로컬(Ollama), API(OpenAI)는 이중 잠금, API 모드에서만 마스킹. API는 실제로 부르지 않는다."""
import sys
from pathlib import Path

import pytest
pytest.importorskip("langchain_openai")
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda, RunnableSequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import tool_calling as tc  # noqa: E402

ENV = ("LLM_PROVIDER", "LLM_ALLOW_EXTERNAL", "LLM_MODEL", "OPENAI_API_KEY", "LLM_TEMPERATURE", "OPENAI_BASE_URL")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def api_on(monkeypatch, **extra):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_the_default_is_the_local_ollama_model_with_no_masking_step():
    assert tc.llm_provider() == "ollama"
    model = tc.get_model()
    assert not isinstance(model, RunnableSequence) and type(model.bound).__name__ == "ChatOllama"


def test_api_mode_needs_both_the_provider_and_the_explicit_external_switch(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(tc.ToolCallingError, match="LLM_ALLOW_EXTERNAL"):
        tc.get_model()


def test_api_mode_without_a_key_says_what_is_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "1")
    with pytest.raises(tc.ToolCallingError, match="OPENAI_API_KEY"):
        tc.get_model()


def test_an_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mystery")
    with pytest.raises(tc.ToolCallingError, match="mystery"):
        tc.get_model()


def test_api_mode_builds_a_deterministic_openai_model_with_the_default_or_chosen_name(monkeypatch):
    api_on(monkeypatch)
    seq = tc.get_model()
    chat = seq.steps[1].bound
    assert type(chat).__name__ == "ChatOpenAI" and chat.model_name == "gpt-4.1-mini" and chat.temperature == 0
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1")
    assert tc.get_model().steps[1].bound.model_name == "gpt-4.1" and tc.active_model_name() == "gpt-4.1"


class StubChat:
    """ChatOpenAI 대신 들어갈 가짜: 받은 메시지를 기록하고, Claude · OpenAI가 줄 수 있는 블록 목록 형태로 답한다."""
    seen: list = []

    def __init__(self, **kw):
        pass

    def bind_tools(self, specs):
        def run(msgs):
            StubChat.seen = list(msgs)
            return AIMessage(content=[{"type": "text", "text": "확인했어요."}, {"type": "text", "text": " 감사합니다."}],
                             tool_calls=[{"name": "search_terms", "args": {"query": "중도해지"}, "id": "c1"}])
        return RunnableLambda(run)


def test_in_api_mode_the_model_only_sees_masked_text_and_the_reply_is_plain_text(monkeypatch):
    api_on(monkeypatch)
    import langchain_openai
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", StubChat)
    original = [SystemMessage("규칙"), HumanMessage("제 번호는 010-1234-5678이고 1000만원을 2026-01-01에 넣었어요")]
    reply = tc.get_model().invoke(original)
    sent = StubChat.seen[1].content
    assert "[전화번호]" in sent and "5678" not in sent and "1000만원" in sent and "2026-01-01" in sent
    assert original[1].content.startswith("제 번호는 010-1234-5678")                      # 호출한 쪽의 메시지는 그대로
    assert reply.content == "확인했어요. 감사합니다." and reply.tool_calls[0]["name"] == "search_terms"      # 블록 목록 → 문자열, 도구 호출은 유지


def test_the_failure_hint_names_the_provider_in_use(monkeypatch):
    assert "ollama serve" in tc.model_failure_hint()
    api_on(monkeypatch)
    hint = tc.model_failure_hint()
    assert "ollama serve" not in hint and "OPENAI_API_KEY" in hint


def test_health_reports_which_provider_and_model_answer_so_an_accidental_api_mode_is_visible(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from app import api
    client = TestClient(api.app)
    monkeypatch.delenv("API_KEY", raising=False)
    local = client.get("/health").json()
    assert local["provider"] == "ollama" and local["model"] == tc.TOOL_MODEL
    api_on(monkeypatch)
    remote = client.get("/health").json()
    assert remote["provider"] == "openai" and remote["model"] == "gpt-4.1-mini"


def test_empty_values_from_compose_fall_back_to_the_defaults(monkeypatch):
    """compose는 비워 둔 변수를 빈 문자열로 넘긴다 (LLM_MODEL= → '')."""
    monkeypatch.setenv("LLM_PROVIDER", "")
    assert tc.llm_provider() == "ollama"
    api_on(monkeypatch, LLM_MODEL="")
    assert tc.active_model_name() == "gpt-4.1-mini" and tc.get_model().steps[1].bound.model_name == "gpt-4.1-mini"


def test_temperature_defaults_to_zero_and_can_be_omitted_for_models_that_reject_it(monkeypatch):
    """gpt-6-luna는 temperature를 받지 않아 400 'Unsupported parameter'를 돌려줬다 (실제 호출에서 확인)."""
    api_on(monkeypatch)
    assert tc.get_model().steps[1].bound.temperature == 0
    monkeypatch.setenv("LLM_TEMPERATURE", "omit")
    assert tc.get_model().steps[1].bound.temperature is None
    monkeypatch.setenv("LLM_TEMPERATURE", "0.3")
    assert tc.get_model().steps[1].bound.temperature == 0.3
    monkeypatch.setenv("LLM_TEMPERATURE", "hot")
    with pytest.raises(tc.ToolCallingError, match="LLM_TEMPERATURE"):
        tc.get_model()


def test_the_failure_hint_mentions_the_temperature_switch_for_api_mode(monkeypatch):
    api_on(monkeypatch)
    assert "LLM_TEMPERATURE=omit" in tc.model_failure_hint()
