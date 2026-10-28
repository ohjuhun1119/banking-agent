"""5일차 Tool Calling 연결 시험: 도구 정의, 모델 호출(가짜 모델 · 실제 ChatOllama 요청 가로채기), 도구 실행."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from langchain_core.messages import AIMessage  # noqa: E402

from app import tool_calling as tc  # noqa: E402
from app.tools.interest_cases import CASES as CALC_CASES  # noqa: E402
import tool_cases  # noqa: E402


class FakeModel:
    """invoke(messages)를 받으면 정해 둔 도구 호출을 돌려주는 가짜 모델. 받은 메시지를 기록한다."""

    def __init__(self, calls=None, content="", error=None):
        self.calls, self.content, self.error, self.seen = calls or [], content, error, []

    def invoke(self, messages):
        self.seen.append(messages)
        if self.error:
            raise self.error
        return AIMessage(content=self.content, tool_calls=[{"name": n, "args": a, "id": f"call_{i}"}
                                                           for i, (n, a) in enumerate(self.calls)])


# ---------- 도구 정의 ----------
@pytest.mark.parametrize("variant", tc.VARIANTS)
def test_specs_have_two_tools_in_openai_format(variant):
    specs = tc.tool_specs(variant)
    assert [s["function"]["name"] for s in specs] == ["search_terms", "calc_interest"]
    assert all(s["type"] == "function" for s in specs)
    search, calc = (s["function"]["parameters"] for s in specs)
    assert list(search["properties"]) == ["query"] and search["required"] == ["query"]   # 상품명은 모델이 정하지 않는다
    assert list(calc["properties"]) == list(tc.CALC_ARGS) and calc["required"] == []      # 모두 선택: 모르는 값은 비운다
    assert calc["properties"]["principal"]["type"] == "integer" and calc["properties"]["base_rate"]["type"] == "number"
    assert "anyOf" not in json.dumps(specs)                                                # Optional이 anyOf로 바뀌지 않게


def test_variants_differ_and_examples_extend_detailed():
    d = {v: tc.tool_specs(v) for v in tc.VARIANTS}
    descs = {v: [s["function"]["description"] for s in d[v]] for v in tc.VARIANTS}
    assert len({json.dumps(x, ensure_ascii=False) for x in descs.values()}) == 3
    for i in range(2):
        assert descs["examples"][i].startswith(descs["detailed"][i]) and len(descs["examples"][i]) > len(descs["detailed"][i])
        assert len(descs["short"][i]) < len(descs["detailed"][i])
    assert "YYYY-MM-DD" in descs["detailed"][1] and "비워" in descs["detailed"][1]


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError):
        tc.tool_specs("long")


def test_examples_do_not_leak_check_questions():
    """examples 설명의 예시가 확인용 질문 · 값과 겹치면 측정이 낙관적이 된다."""
    text = json.dumps(tc.tool_specs("examples"), ensure_ascii=False)
    quoted = re.findall(r"'([^']{6,})'", text)
    assert quoted, "예시 문장을 찾지 못했다"
    for case in tool_cases.CASES:
        assert case["question"] not in text
        for q in quoted:
            assert q not in case["question"], (q, case["id"])
        for v in case["expect"].get("args", {}).values():
            if isinstance(v, str):
                assert v not in text, (v, case["id"])


def test_system_prompt_carries_today():
    msgs = tc.build_messages("질문", today="2026-10-28")
    assert msgs[0]["role"] == "system" and "2026-10-28" in msgs[0]["content"] and msgs[1]["content"] == "질문"


# ---------- 모델 호출 ----------
def test_ask_returns_tool_calls_without_running_them():
    m = FakeModel([("calc_interest", {"principal": 10000000})])
    out = tc.ask("1000만원 이자", "detailed", "2026-10-28", model=m)
    assert out["tool_calls"] == [{"name": "calc_interest", "args": {"principal": 10000000}}]
    assert out["variant"] == "detailed" and "tool_results" not in out and isinstance(out["ms"], int)
    sys_msg = m.seen[0][0]
    assert "2026-10-28" in sys_msg.content


def test_ask_without_calls_keeps_text():
    out = tc.ask("안녕하세요", model=FakeModel(content="안녕하세요!"))
    assert out["tool_calls"] == [] and out["content"] == "안녕하세요!"


def test_ask_wraps_model_failure():
    with pytest.raises(tc.ToolCallingError) as e:
        tc.ask("질문", model=FakeModel(error=ConnectionError("refused")))
    assert "ollama" in str(e.value).lower()


def test_real_chatollama_request_is_what_we_expect(monkeypatch):
    """실제 ChatOllama가 Ollama 서버로 보내는 요청을 가로채, 도구 정의 · 메시지 · 옵션이 그대로 가는지 확인한다."""
    pytest.importorskip("langchain_ollama")
    import ollama
    sent = {}

    def fake_chat(self, **kw):
        sent.update(kw)
        chunk = {"model": tc.TOOL_MODEL, "created_at": "2026-10-28T00:00:00Z", "done": True, "done_reason": "stop",
                 "message": {"role": "assistant", "content": "",
                             "tool_calls": [{"function": {"name": "calc_interest",
                                                          "arguments": {"principal": 10000000, "start_date": "2026-01-01"}}}]}}
        return iter([chunk]) if kw.get("stream") else chunk

    monkeypatch.setattr(ollama.Client, "chat", fake_chat)
    out = tc.ask("1000만원을 2026-01-01에 넣었어요", "short", "2026-10-28")
    assert out["tool_calls"] == [{"name": "calc_interest", "args": {"principal": 10000000, "start_date": "2026-01-01"}}]
    assert sent["model"] == tc.TOOL_MODEL
    assert sent["options"]["temperature"] == 0 and sent["options"]["num_ctx"] == tc.NUM_CTX
    tools = [t if isinstance(t, dict) else t.model_dump(exclude_none=True) for t in sent["tools"]]
    assert [t["function"]["name"] for t in tools] == ["search_terms", "calc_interest"]
    assert tools[1]["function"]["description"] == tc.tool_specs("short")[1]["function"]["description"]
    roles = [(m["role"] if isinstance(m, dict) else m.role) for m in sent["messages"]]
    assert roles == ["system", "user"]


# ---------- 도구 실행 ----------
def _fake_search(calls):
    def fn(query, product=None, k=5, **kw):
        calls.append({"query": query, "product": product, "k": k})
        return {"status": "ok", "query": query, "mode": "dense", "ms": {"total": 3},
                "results": [{"rank": 1, "chunk_id": "x", "distance": 0.3, "chunk_type": "중도해지", "text": "본문",
                             "source": {"doc_title": "설명서"}, "scores": {"dense_rank": 1}}]}
    return fn


def test_search_product_is_fixed_by_code():
    seen = []
    res = tc.run_tool("search_terms", {"query": " 중도해지 금리 ", "product": "정기예금"}, search_fn=_fake_search(seen))
    assert seen == [{"query": "중도해지 금리", "product": "하나의 정기예금", "k": 5}]
    assert res["status"] == "ok" and res["results"][0]["text"] == "본문" and "scores" not in res["results"][0]
    assert "product" in res["warnings"][0]


def test_search_empty_query_is_invalid():
    for args in ({}, {"query": ""}, {"query": "  "}, {"query": 3}):
        assert tc.run_tool("search_terms", args, search_fn=_fake_search([]))["status"] == "invalid_input"


def test_search_not_found_and_failures_become_status():
    nf = tc.run_tool("search_terms", {"query": "대출"}, search_fn=lambda q, **kw: {"status": "not_found", "query": q, "results": []})
    assert nf["status"] == "not_found"

    def boom(q, **kw):
        raise SystemExit("Ollama에 연결할 수 없습니다")
    assert tc.run_tool("search_terms", {"query": "a"}, search_fn=boom)["status"] == "error"

    def boom2(q, **kw):
        raise ValueError("x")
    assert tc.run_tool("search_terms", {"query": "a"}, search_fn=boom2)["status"] == "error"


@pytest.mark.parametrize("case", CALC_CASES, ids=[c["id"] for c in CALC_CASES])
def test_calc_runs_with_model_style_args(case):
    args = {k: case[k] for k in tc.CALC_ARGS if case[k] is not None}
    res = tc.run_tool("calc_interest", args)
    assert res["status"] == "ok" and res["interest_krw"] == case["interest"]


def test_calc_status_paths():
    assert tc.run_tool("calc_interest", {})["status"] == "missing_info"
    assert tc.run_tool("calc_interest", {"principal": "1000만원"})["status"] == "invalid_input"
    r = tc.run_tool("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                                      "termination_date": "2026-12-07"})
    assert r["status"] == "need_base_rate"
    r = tc.run_tool("calc_interest", {"principal": 100000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                                      "termination_date": "2026-05-31"})
    assert r["status"] == "invalid_input" and r["message"].startswith("최소 가입금액은 100만원입니다.")


def test_calc_unknown_argument_is_rejected_not_crashed():
    r = tc.run_tool("calc_interest", {"principal": 10000000, "days": 150})
    assert r["status"] == "invalid_input" and "days" in r["message"]


def test_unknown_tool():
    r = tc.run_tool("transfer_money", {"amount": 1})
    assert r["status"] == "unknown_tool" and "search_terms" in r["message"]


def test_ask_and_run_executes_each_call():
    m = FakeModel([("search_terms", {"query": "중도해지"}), ("calc_interest", {})])
    out = tc.ask_and_run("q", model=m, search_fn=_fake_search([]))
    assert [r["name"] for r in out["tool_results"]] == ["search_terms", "calc_interest"]
    assert [r["result"]["status"] for r in out["tool_results"]] == ["ok", "missing_info"]
