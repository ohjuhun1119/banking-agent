"""6일차 그래프 시험: agent ⇄ tools 반복, 세션 유지, 호출 횟수 제한, 대기 시간 제한과 재시도, 모델 실패 처리.
모델은 가짜로 바꿔 끼우고, 실제 ChatOllama가 서버로 보내는 요청은 따로 가로채서 확인한다."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from app import graph as g  # noqa: E402
from app import tool_calling as tc  # noqa: E402

BASE = {"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01"}
_ids = iter(range(10_000))


def call(name, **args):
    return {"name": name, "args": args, "id": f"call_{next(_ids)}"}


def ai(*calls, text=""):
    return AIMessage(content=text, tool_calls=list(calls))


class Script:
    """fn(messages, n) -> AIMessage. 모델에 실제로 들어간 메시지를 seen에 기록한다."""

    def __init__(self, fn):
        self.fn, self.n, self.seen = fn, 0, []

    def invoke(self, messages):
        self.n += 1
        self.seen.append(list(messages))
        return self.fn(messages, self.n)


def last_tool_json(messages):
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    return json.loads(tool_msgs[-1].content)


def search_ok(query, product=None, k=5, **kw):
    return {"status": "ok", "query": query, "ms": {}, "results": [
        {"rank": 1, "chunk_id": "x", "distance": 0.3, "chunk_type": "중도해지", "text": f"본문:{query}", "source": {"doc_title": "설명서"}, "scores": {}}]}


def agent(fn, **kw):
    kw.setdefault("search_fn", search_ok)
    m = Script(fn)
    return g.Agent(model=m, **kw), m


# ---------- 기본 흐름 ----------
def test_plain_answer_without_tools():
    a, m = agent(lambda msgs, n: AIMessage(content="안녕하세요!"))
    res = a.ask("안녕하세요", "s")
    assert res["answer"] == "안녕하세요!" and res["tool_calls"] == 0 and res["model_calls"] == 1
    assert res["trace"] == [] and res["error"] is None and res["stop_reason"] is None and res["confirmed"] == {}


def test_tool_result_goes_back_to_the_model_and_final_answer_follows():
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", **BASE, termination_date="2026-07-20", base_rate=3.5))
        return AIMessage(content="세전 이자는 63,013원입니다.")
    a, m = agent(fn)
    res = a.ask("이자 계산해줘", "s", today="2025-03-04")
    assert "2025-03-04" in m.seen[0][0].content
    assert res["answer"] == "세전 이자는 63,013원입니다." and (res["tool_calls"], res["model_calls"]) == (1, 2)
    result = last_tool_json(m.seen[1])                                   # 두 번째 호출에서 모델이 본 도구 결과
    assert result["status"] == "ok" and result["interest_krw"] == 63013 and result["formula"] and result["assumptions"]
    assert [t["status"] for t in res["trace"]] == ["ok"]
    assert res["confirmed"]["base_rate"] == 3.5


def test_search_tool_runs_with_fixed_product_and_returns_compact_results():
    seen = []

    def spy(query, product=None, k=5, **kw):
        seen.append((query, product, k))
        return search_ok(query)
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="중도해지 금리", product="정기예금")) if n == 1 else AIMessage(content="끝"), search_fn=spy)
    a.ask("중도해지?", "s")
    assert seen == [("중도해지 금리", "하나의 정기예금", 5)]
    result = last_tool_json(m.seen[1])
    assert result["status"] == "ok" and result["results"][0]["text"] == "본문:중도해지 금리" and "scores" not in result["results"][0]


def test_parallel_tool_calls_are_all_answered_in_order():
    c1, c2 = call("search_terms", query="a"), call("search_terms", query="b")
    a, m = agent(lambda msgs, n: ai(c1, c2) if n == 1 else AIMessage(content="끝"))
    res = a.ask("q", "s")
    tool_msgs = [x for x in m.seen[1] if isinstance(x, ToolMessage)]
    assert [x.tool_call_id for x in tool_msgs] == [c1["id"], c2["id"]] and res["tool_calls"] == 2


def test_unknown_tool_becomes_a_status_the_model_can_read():
    a, m = agent(lambda msgs, n: ai(call("transfer_money", amount=1)) if n == 1 else AIMessage(content="끝"))
    res = a.ask("q", "s")
    assert last_tool_json(m.seen[1])["status"] == "unknown_tool" and res["answer"] == "끝"


def test_empty_model_reply_gets_a_fallback_message():
    a, _ = agent(lambda msgs, n: AIMessage(content="  "))
    res = a.ask("q", "s")
    assert res["answer"] == g.EMPTY_TEXT


# ---------- 세션과 확인된 값 ----------
def test_followup_turn_keeps_history_and_confirmed_values():
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", **BASE, termination_date="2026-12-07"))
        if n == 2:
            return AIMessage(content="신규일 당시 기본금리(%)를 알려주세요.")
        if n == 3:
            return ai(call("calc_interest", **BASE, termination_date="2026-12-07", base_rate=3.5))
        return AIMessage(content="세전 이자는 273,304원입니다.")
    a, m = agent(fn)
    r1 = a.ask("1000만원 2026-01-01 신규 2027-01-01 만기, 2026-12-07 해지하면 이자는?", "s")
    assert last_tool_json(m.seen[1])["status"] == "need_base_rate" and "기본금리" in r1["answer"]
    assert r1["confirmed"] == {**BASE, "termination_date": "2026-12-07"}
    r2 = a.ask("기본금리는 3.5%였어요", "s")
    assert r2["answer"] == "세전 이자는 273,304원입니다." and last_tool_json(m.seen[3])["interest_krw"] == 273304
    turn2 = m.seen[2]                                                    # 두 번째 질문의 첫 모델 호출
    assert isinstance(turn2[0], SystemMessage) and "[지금까지 확인된 값] 원금 10,000,000원 · 신규일 2026-01-01" in turn2[0].content
    assert [x.content for x in turn2 if isinstance(x, HumanMessage)][0].startswith("1000만원 2026-01-01")   # 앞 질문이 남아 있다
    assert r2["tool_calls"] == 1 and r2["confirmed"]["base_rate"] == 3.5                                     # 횟수는 질문마다 새로 센다
    assert len(r1["trace"]) == 1 and len(r2["trace"]) == 1 and r2["trace"][0]["args"]["base_rate"] == 3.5  # 기록도 이번 질문 것만


def test_first_turn_has_no_confirmed_block_and_sessions_are_isolated():
    a, m = agent(lambda msgs, n: ai(call("calc_interest", **BASE)) if n == 1 else AIMessage(content="끝"))
    a.ask("q1", "A")
    assert "[지금까지 확인된 값]" not in m.seen[0][0].content
    b, m2 = agent(lambda msgs, n: AIMessage(content="끝"))
    b.checkpointer = a.checkpointer                                     # 같은 저장소라도 session_id가 다르면 보이지 않는다
    b.graph = g.build_graph(model=m2, checkpointer=a.checkpointer)
    b.ask("q2", "B")
    assert "[지금까지 확인된 값]" not in m2.seen[0][0].content and len(b.history("B")) == 2
    b.ask("q3", "A")
    assert "[지금까지 확인된 값] 원금 10,000,000원" in m2.seen[1][0].content


def test_invalid_values_are_not_remembered_and_reset_forgets_everything():
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal="1000만원", start_date="2026-01-01")) if n == 1 else AIMessage(content="끝"))
    res = a.ask("q", "s")
    assert res["confirmed"] == {"start_date": "2026-01-01"} and res["trace"][0]["status"] == "invalid_input"
    assert len(a.history("s")) == 4
    a.reset("s")
    assert a.history("s") == []


def test_old_search_text_is_shortened_but_current_turn_is_not():
    long = "가" * 2000
    msgs = [HumanMessage("첫 질문"), AIMessage(content="", tool_calls=[call("search_terms", query="a")]),
            ToolMessage(content=long, tool_call_id="1", name="search_terms"), AIMessage(content="답"),
            HumanMessage("둘째 질문"), AIMessage(content="", tool_calls=[call("search_terms", query="b")]),
            ToolMessage(content=long, tool_call_id="2", name="search_terms")]
    out = g.prompt_messages({"messages": msgs, "today": "2026-10-28"})
    tool_texts = [m.content for m in out if isinstance(m, ToolMessage)]
    assert len(tool_texts[0]) < 400 and "줄임" in tool_texts[0] and tool_texts[1] == long
    assert msgs[2].content == long                                       # 저장된 대화는 건드리지 않는다


def test_system_text_has_today_rules_and_optional_confirmed_block():
    t = g.system_text({"today": "2025-03-04"})
    assert "2025-03-04" in t and "need_base_rate" in t and "[지금까지 확인된 값]" not in t
    t = g.system_text({"today": "2025-03-04", "confirmed": {"principal": 5_000_000}})
    assert "[지금까지 확인된 값] 원금 5,000,000원" in t and "새로운 계산을 시작한다고 하면" in t


# ---------- 호출 횟수 제한 ----------
def test_tool_call_limit_stops_the_loop_and_suggests_a_human():
    executed = []

    def spy(query, **kw):
        executed.append(query)
        return search_ok(query)
    a, m = agent(lambda msgs, n: ai(call("search_terms", query=f"검색{n}")), search_fn=spy)
    res = a.ask("q", "s")
    assert res["stop_reason"] == "tool_limit" and res["tool_calls"] == 5 and executed == [f"검색{i}" for i in range(1, 6)]
    assert res["answer"] == g.LIMIT_TEXT and "상담원" in res["answer"] and m.n == 6
    hist = a.history("s")
    assert isinstance(hist[-3], AIMessage) and hist[-3].tool_calls                        # 부르려던 호출
    assert json.loads(hist[-2].content)["status"] == "skipped" and hist[-2].tool_call_id == hist[-3].tool_calls[0]["id"]
    assert hist[-1].content == g.LIMIT_TEXT


def test_limit_applies_to_one_message_with_many_calls_and_counts_reset_next_turn():
    executed = []
    a, m = agent(lambda msgs, n: ai(*[call("search_terms", query=f"q{i}") for i in range(3)]) if n == 1 else AIMessage(content="끝"),
                 max_tool_calls=2, search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = a.ask("q", "s")
    assert res["stop_reason"] == "tool_limit" and res["tool_calls"] == 0 and executed == []
    assert len([x for x in a.history("s") if isinstance(x, ToolMessage)]) == 3
    res2 = a.ask("다시", "s")                                                             # 다음 질문은 한도가 새로 시작
    assert res2["answer"] == "끝" and res2["stop_reason"] is None


def test_repeated_identical_call_is_not_executed_again():
    executed = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="같은 검색")) if n <= 2 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = a.ask("q", "s")
    assert executed == ["같은 검색"] and res["tool_calls"] == 2
    assert [(t["status"], t["duplicate"]) for t in res["trace"]] == [("ok", False), ("duplicate", True)]
    assert last_tool_json(m.seen[2])["status"] == "duplicate"


def test_same_call_in_the_next_turn_is_not_a_duplicate():
    executed = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="같은 검색")) if n in (1, 3) else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    a.ask("첫 질문", "s")
    res = a.ask("같은 걸 다시", "s")
    assert executed == ["같은 검색", "같은 검색"] and res["trace"][0]["duplicate"] is False and res["trace"][0]["status"] == "ok"


def test_same_call_with_other_values_is_a_new_call():
    executed = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query=f"검색{n}")) if n <= 2 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    a.ask("q", "s")
    assert executed == ["검색1", "검색2"]


# ---------- 대기 시간 제한과 재시도 ----------
def test_timeout_is_retried_once_then_reported_as_error():
    calls = []

    def slow(query, **kw):
        calls.append(query)
        time.sleep(0.4)
        return search_ok(query)
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="느린 검색")) if n == 1 else AIMessage(content="끝"), search_fn=slow, tool_timeout=0.05)
    t0 = time.perf_counter()
    res = a.ask("q", "s")
    assert time.perf_counter() - t0 < 0.38                                # 한 번 기다린 시간(0.05초)의 두 배 정도만 걸린다
    result = last_tool_json(m.seen[1])
    assert result["status"] == "error" and result["reason"] == "timeout" and result["attempts"] == 2 and "시간 초과" in result["message"]
    assert res["trace"][0]["retried"] is True and calls == ["느린 검색", "느린 검색"]
    time.sleep(0.5)                                                       # 남은 스레드가 끝나도 문제가 없어야 한다


def test_failure_then_success_on_retry_gives_the_good_result():
    attempts = []

    def flaky(query, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("일시적 오류")
        return search_ok(query)
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="q")) if n == 1 else AIMessage(content="끝"), search_fn=flaky)
    res = a.ask("q", "s")
    assert last_tool_json(m.seen[1])["status"] == "ok" and len(attempts) == 2
    assert res["trace"][0]["retried"] is True and res["trace"][0]["status"] == "ok"


def test_permanent_failure_returns_error_after_two_attempts():
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="q")) if n == 1 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: {"status": "error", "message": "인덱스가 비어 있습니다."})
    a.ask("q", "s")
    result = last_tool_json(m.seen[1])
    assert result["status"] == "error" and result["attempts"] == 2 and "인덱스" in result["message"]


def test_input_errors_are_not_retried(monkeypatch):
    n_calls = []

    def fake_run(name, args, search_fn=None):
        n_calls.append(name)
        return {"status": "invalid_input", "message": "x"}
    monkeypatch.setattr(tc, "run_tool", fake_run)
    result, retried = g.run_guarded("calc_interest", {}, timeout=1)
    assert result["status"] == "invalid_input" and retried is False and n_calls == ["calc_interest"]


def test_not_found_is_not_retried():
    n = []
    a, _ = agent(lambda msgs, k: ai(call("search_terms", query="대출")) if k == 1 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: n.append(1) or {"status": "not_found", "query": q, "results": []})
    res = a.ask("q", "s")
    assert len(n) == 1 and res["trace"][0]["status"] == "not_found" and res["trace"][0]["retried"] is False


# ---------- 모델 호출 실패 ----------
def test_model_failure_rolls_back_the_turn_and_next_question_works():
    def fn(msgs, n):
        if n == 1:
            raise ConnectionError("refused")
        return AIMessage(content="이번엔 됩니다")
    a, m = agent(fn)
    res = a.ask("첫 시도", "s")
    assert res["answer"] is None and res["stop_reason"] == "model_error" and "ollama" in res["error"].lower()
    assert a.history("s") == []                                           # 실패한 질문이 대화에 남지 않는다
    res2 = a.ask("두 번째 시도", "s")
    assert res2["answer"] == "이번엔 됩니다" and res2["error"] is None
    humans = [x.content for x in m.seen[1] if isinstance(x, HumanMessage)]
    assert humans == ["두 번째 시도"]


def test_model_failure_after_a_tool_call_removes_the_whole_turn():
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", **BASE))
        raise TimeoutError("모델 응답 없음")
    a, _ = agent(fn)
    res = a.ask("q", "s")
    assert res["error"] and res["answer"] is None and res["tool_calls"] == 1 and a.history("s") == []


# ---------- 실제 ChatOllama와 함께 (서버 요청 가로채기) ----------
def test_real_chatollama_gets_tool_result_in_the_second_request(monkeypatch):
    pytest.importorskip("langchain_ollama")
    import ollama
    sent = []

    def fake_chat(self, **kw):
        sent.append(kw)
        if len(sent) == 1:
            msg = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "calc_interest", "arguments": {
                **BASE, "termination_date": "2026-07-20", "base_rate": 3.5}}}]}
        else:
            msg = {"role": "assistant", "content": "세전 이자는 63,013원입니다."}
        chunk = {"model": tc.TOOL_MODEL, "created_at": "2026-10-28T00:00:00Z", "done": True, "done_reason": "stop", "message": msg}
        return iter([chunk]) if kw.get("stream") else chunk

    monkeypatch.setattr(ollama.Client, "chat", fake_chat)
    a = g.Agent(variant="short")
    res = a.ask("1000만원 2026-01-01 신규 2027-01-01 만기 2026-07-20 해지 기본금리 3.5%", "s", today="2025-03-04")
    assert res["answer"] == "세전 이자는 63,013원입니다." and len(sent) == 2
    second = [m if isinstance(m, dict) else m.model_dump() for m in sent[1]["messages"]]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["function"]["name"] == "calc_interest"
    assert json.loads(second[3]["content"])["interest_krw"] == 63013
    assert "2025-03-04" in second[0]["content"] and sent[1]["options"]["temperature"] == 0
    assert [t["function"]["name"] for t in sent[1]["tools"]] == ["search_terms", "calc_interest"]


# ---------- 입력 검사 ----------
def test_bad_today_is_rejected():
    a, _ = agent(lambda msgs, n: AIMessage(content="끝"))
    with pytest.raises(ValueError):
        a.ask("q", "s", today="2026-13-45")


def test_cli_one_shot_and_chat(monkeypatch, capsys):
    class FakeAgent:
        def __init__(self, **kw):
            self.asked = []

        def ask(self, q, session, today):
            self.asked.append((q, session))
            return {"answer": f"답:{q}", "error": None, "stop_reason": None, "tool_calls": 1, "model_calls": 2, "ms": 5,
                    "confirmed_text": "원금 10,000,000원",
                    "trace": [{"name": "calc_interest", "args": {"principal": 1}, "status": "ok", "ms": 1, "retried": True, "duplicate": False}]}

        def reset(self, session):
            self.asked.append(("/reset", session))
    fake = FakeAgent()
    monkeypatch.setattr(g, "Agent", lambda **kw: fake)
    answers = iter(["질문1", "/new", "질문2", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    g.main(["--chat", "--session", "t"])
    out = capsys.readouterr().out
    assert fake.asked == [("질문1", "t"), ("/reset", "t"), ("질문2", "t")]
    assert "답:질문1" in out and "calc_interest" in out and "(재시도함)" in out and "확인된 값: 원금 10,000,000원" in out
    with pytest.raises(SystemExit):
        g.main([])                                                         # 질문도 --chat도 없으면 사용법을 보여준다
