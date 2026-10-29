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
SAID = "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요."          # BASE의 값을 사용자가 말한 문장
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
    res = a.ask(SAID + " 2026-07-20에 해지하면 이자는? 기본금리는 3.5%였어요.", "s", today="2025-03-04")
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
    a.ask(SAID, "A")
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
    res = a.ask("1000만원을 2026-01-01에 넣었어요", "s")
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
    res = a.ask(SAID, "s")
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
                    "trace": [{"name": "calc_interest", "args": {"principal": 1}, "status": "ok", "ms": 1, "retried": True, "duplicate": False,
                       "dropped": {"base_rate": 3.5}}]}

        def reset(self, session):
            self.asked.append(("/reset", session))
    fake = FakeAgent()
    monkeypatch.setattr(g, "Agent", lambda **kw: fake)
    answers = iter(["질문1", "/new", "질문2", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    g.main(["--chat", "--session", "t"])
    out = capsys.readouterr().out
    assert fake.asked == [("질문1", "t"), ("/reset", "t"), ("질문2", "t")]
    assert "답:질문1" in out and "calc_interest" in out and "(재시도함)" in out and "말하지 않은 값 제외: 기본금리" in out and "확인된 값: 원금 10,000,000원" in out
    with pytest.raises(SystemExit):
        g.main([])                                                         # 질문도 --chat도 없으면 사용법을 보여준다


# ---------- 값 근거 대조 (말하지 않은 값은 도구에 넘기지 않는다) ----------
USER_Q = "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요?"
INVENTED = {**BASE, "termination_date": "2026-12-07", "base_rate": 3.5}         # 사용자가 말하지 않은 기본금리 3.5를 모델이 넣음 (실제 대화에서 발생)


def test_invented_base_rate_is_dropped_so_the_tool_asks_instead_of_calculating():
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", **INVENTED))
        return AIMessage(content="신규일 당시 기본금리(%)를 알려주세요.")
    a, m = agent(fn)
    res = a.ask(USER_Q, "s", today="2026-10-28")
    result = last_tool_json(m.seen[1])
    assert result["status"] == "need_base_rate" and "interest_krw" not in result                   # 계산하지 않고 되묻는다
    assert result["dropped_args"] == ["기본금리"] and "3.5" not in json.dumps(result, ensure_ascii=False)         # 모델이 따라 말하지 않게 값은 알리지 않는다
    assert "기본금리" in result["note"]
    assert res["trace"][0]["dropped"] == {"base_rate": 3.5} and res["trace"][0]["status"] == "need_base_rate"
    assert "base_rate" not in res["confirmed"] and res["confirmed"]["termination_date"] == "2026-12-07"
    assert "기본금리" in res["answer"]


def test_after_the_user_gives_the_rate_the_same_call_is_calculated():
    def fn(msgs, n):
        if n in (1, 3):
            return ai(call("calc_interest", **INVENTED))
        return AIMessage(content="되묻기" if n == 2 else "세전 이자는 273,304원입니다.")
    a, m = agent(fn)
    a.ask(USER_Q, "s", today="2026-10-28")
    res = a.ask("기본금리는 3.5%였어요", "s", today="2026-10-28")
    result = last_tool_json(m.seen[3])
    assert result["status"] == "ok" and result["interest_krw"] == 273304 and "dropped_args" not in result
    assert res["trace"][0]["dropped"] == {} and res["confirmed"]["base_rate"] == 3.5


def test_values_said_in_an_earlier_turn_still_count():
    def fn(msgs, n):
        if n == 1:
            return AIMessage(content="해지일도 알려주세요.")
        if n == 2:
            return ai(call("calc_interest", **BASE, termination_date="2026-01-21"))
        return AIMessage(content="끝")
    a, m = agent(fn)
    a.ask(SAID, "s")
    res = a.ask("2026-01-21에 해지해요", "s")                                # 원금 · 신규일 · 만기일은 앞 질문에서 말함
    assert res["trace"][0]["dropped"] == {} and res["trace"][0]["status"] == "ok"


def test_relative_date_computed_by_the_model_is_dropped():
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", principal=20_000_000, start_date="2026-05-28", termination_date="2026-10-28"))
        return AIMessage(content="정확한 신규일을 알려주세요.")
    a, m = agent(fn)
    res = a.ask("5개월 전에 2000만원을 1년짜리로 가입했어요. 지금 해지하면 이자는요?", "s", today="2026-10-28")
    assert res["trace"][0]["dropped"] == {"start_date": "2026-05-28"}                    # '5개월 전'을 날짜로 바꿔 넣은 것
    assert res["confirmed"] == {"principal": 20_000_000, "termination_date": "2026-10-28"}   # 지금 = 오늘, 원금은 말한 값
    assert last_tool_json(m.seen[1])["status"] == "missing_info"


def test_search_arguments_are_not_checked():
    seen = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="모델이 만든 검색어")) if n == 1 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: seen.append(q) or search_ok(q))
    res = a.ask("해지하면 어떻게 돼요?", "s")
    assert seen == ["모델이 만든 검색어"] and res["trace"][0]["dropped"] == {}


def test_repeating_the_same_invented_call_is_still_caught_as_duplicate():
    a, m = agent(lambda msgs, n: ai(call("calc_interest", **INVENTED)) if n <= 2 else AIMessage(content="끝"))
    res = a.ask(USER_Q, "s", today="2026-10-28")
    assert [(t["status"], t["duplicate"]) for t in res["trace"]] == [("need_base_rate", False), ("duplicate", True)]


def test_confirmed_values_count_even_when_the_turn_that_said_them_was_rolled_back():
    """첫 질문에서 값을 확인한 뒤 모델 호출이 실패하면 그 질문은 대화에서 지워지지만 확인된 값은 남는다.
    모델은 시스템 프롬프트로 그 값을 보고 다시 넘기므로, 근거 대조가 확인된 값도 근거로 인정해야 한다."""
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", **BASE))
        if n == 2:
            raise TimeoutError("모델 응답 없음")                              # 첫 질문의 두 번째 모델 호출이 실패 -> 질문이 지워짐
        if n == 3:
            return ai(call("calc_interest", **BASE, termination_date="2026-01-21"))
        return AIMessage(content="끝")
    a, m = agent(fn)
    assert a.ask(SAID, "s")["error"] and a.history("s") == []
    res = a.ask("2026-01-21에 해지해요", "s")
    assert "원금 10,000,000원" in m.seen[2][0].content                        # 모델이 확인된 값을 본다
    assert res["trace"][0]["dropped"] == {} and res["trace"][0]["status"] == "ok"


def test_today_word_uses_the_date_given_to_the_agent():
    """'오늘 해지하면'의 오늘은 ask(today=...)로 알려준 날짜다 (실제 오늘과 다른 날짜로 시험한다)."""
    a, m = agent(lambda msgs, n: ai(call("calc_interest", **BASE, termination_date="2025-03-04")) if n == 1 else AIMessage(content="끝"))
    res = a.ask(SAID + " 오늘 해지하면 이자는?", "s", today="2025-03-04")
    assert res["trace"][0]["dropped"] == {}
    b, _ = agent(lambda msgs, n: ai(call("calc_interest", **BASE, termination_date="2025-03-05")) if n == 1 else AIMessage(content="끝"))
    assert b.ask(SAID + " 오늘 해지하면 이자는?", "s", today="2025-03-04")["trace"][0]["dropped"] == {"termination_date": "2025-03-05"}


def test_when_the_model_repeats_the_tool_message_the_user_is_asked_for_the_rate():
    """실제 모델은 되묻는 도구 메시지를 거의 그대로 읽어 준다. 그 메시지가 질문이어야 사용자가 무엇을 답할지 안다."""
    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", **BASE, termination_date="2026-12-07"))
        return AIMessage(content=last_tool_json(msgs)["message"])
    a, _ = agent(fn)
    res = a.ask(USER_Q, "s", today="2026-10-28")
    assert "기본금리(연 %)는 얼마였나요?" in res["answer"] and res["trace"][0]["status"] == "need_base_rate"


def test_rules_tell_the_model_to_end_clarifications_with_a_question():
    t = g.system_text({"today": "2025-03-04"})
    assert "질문으로 끝낸다" in t and "얼마였는지 사용자에게 질문한다" in t


# ---------- 답변 점검 · 상담원 연결 (실제 대화에서 나온 오류) ----------
KOREAN_OK = "하나은행 고객센터나 영업점에 문의해 주세요."
REAL_CHINESE_REPLY = "잠시 기다려 주세요. 상담원이 연결될 때까지 기다려 주십시오. 또는 고객센터 번호 1544-1111을 직접拨打以转接人工客服。"
REAL_FABRICATED_REPLY = "상담원 연결을 요청해주시면 됩니다. 고객센터 번호 1544-1111을 통해 상담원과 연결하실 수 있습니다."


@pytest.mark.parametrize("question", ["상담원 연결해줘", "상담원 연결 요청"])        # 스크린샷의 두 질문
def test_handoff_request_is_answered_without_the_model_and_without_promising_anything(question):
    a, m = agent(lambda msgs, n: AIMessage(content=REAL_FABRICATED_REPLY))
    res = a.ask(question, "s")
    assert m.n == 0 and res["model_calls"] == 0 and res["tool_calls"] == 0                   # 모델을 부르지 않는다
    assert res["answer"] == g.HANDOFF_TEXT and res["stop_reason"] == "handoff_not_ready" and res["error"] is None
    assert "아직 지원하지 않아요" in res["answer"] and "1544" not in res["answer"]
    for promise in ("잠시 기다려", "연결될 때까지", "접수", "연결해 드릴게요"):                    # 연결해 주는 것처럼 말하지 않는다
        assert promise not in res["answer"]
    assert [type(x).__name__ for x in a.history("s")] == ["HumanMessage", "AIMessage"]


def test_conversation_continues_normally_after_a_handoff_request():
    a, m = agent(lambda msgs, n: AIMessage(content="일반 답변"))
    a.ask("안녕하세요", "s")
    handoff = a.ask("상담원 연결해줘", "s")
    after = a.ask("그럼 다른 걸 물어볼게요", "s")
    assert handoff["stop_reason"] == "handoff_not_ready" and m.n == 2                         # 일반 질문 둘만 모델을 불렀다
    assert after["answer"] == "일반 답변" and after["stop_reason"] is None and after["model_calls"] == 1
    assert len(a.history("s")) == 6


def test_chinese_in_the_answer_is_retried_once_in_korean():
    a, m = agent(lambda msgs, n: AIMessage(content=REAL_CHINESE_REPLY if n == 1 else KOREAN_OK))
    res = a.ask("이거 뭐예요?", "s")
    assert res["answer"] == KOREAN_OK and res["model_calls"] == 2
    retry_note = m.seen[1][-1]
    assert isinstance(retry_note, SystemMessage) and "한국어" in retry_note.content and "중국어" in retry_note.content
    assert not any(isinstance(x, SystemMessage) and "직전 답변" in x.content for x in a.history("s"))      # 점검용 메모는 대화에 남지 않는다


def test_chinese_that_survives_the_retry_is_replaced_by_a_fixed_korean_message():
    a, m = agent(lambda msgs, n: AIMessage(content=REAL_CHINESE_REPLY))
    res = a.ask("이거 뭐예요?", "s")
    assert res["answer"] == g.UNSAFE_TEXT and res["model_calls"] == 2 and m.n == 2 and "拨" not in res["answer"]


def test_invented_phone_number_is_retried_and_then_blocked():
    a, m = agent(lambda msgs, n: AIMessage(content=REAL_FABRICATED_REPLY if n == 1 else KOREAN_OK))
    res = a.ask("문의처가 어디예요?", "s")
    assert res["answer"] == KOREAN_OK and "근거 없는 전화번호" in m.seen[1][-1].content
    b, mb = agent(lambda msgs, n: AIMessage(content=REAL_FABRICATED_REPLY))
    blocked = b.ask("문의처가 어디예요?", "s")
    assert blocked["answer"] == g.UNSAFE_TEXT and "1544" not in blocked["answer"] and mb.n == 2


def test_phone_number_from_a_tool_result_or_from_the_user_is_allowed():
    def search_with_number(query, product=None, k=5, **kw):
        r = search_ok(query)
        r["results"][0]["text"] = "고객센터(1599-1111)로 문의하세요."
        return r
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="문의처")) if n == 1 else AIMessage(content="고객센터 1599-1111로 문의하세요."),
                 search_fn=search_with_number)
    res = a.ask("문의처가 어디예요?", "s")
    assert res["answer"] == "고객센터 1599-1111로 문의하세요." and res["model_calls"] == 2         # 다시 시키지 않았다 (도구 호출 1 + 답변 1)
    b, mb = agent(lambda msgs, n: AIMessage(content="말씀하신 1588-1111로 문의하세요."))
    assert b.ask("제가 아는 번호는 1588-1111인데 맞나요?", "s")["model_calls"] == 1


def test_phone_number_from_an_old_tool_result_does_not_count_in_a_later_turn():
    """이전 질문의 도구 결과에 있던 번호는 이번 질문의 근거가 아니다 (이번 질문에서 다시 검색해야 한다)."""
    def search_with_number(query, product=None, k=5, **kw):
        r = search_ok(query)
        r["results"][0]["text"] = "고객센터(1599-1111)"
        return r

    def fn(msgs, n):
        if n == 1:
            return ai(call("search_terms", query="문의처"))
        if n == 2:
            return AIMessage(content="첫 답")
        return AIMessage(content="1599-1111로 문의하세요." if n == 3 else KOREAN_OK)
    a, m = agent(fn, search_fn=search_with_number)
    a.ask("문의처가 어디예요?", "s")
    res = a.ask("번호만 다시 알려줘", "s")
    assert res["answer"] == KOREAN_OK and res["model_calls"] == 2


def test_chinese_search_query_is_retried_before_the_tool_runs():
    executed = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="中途解约利率")) if n == 1
                 else ai(call("search_terms", query="중도해지 이율")) if n == 2 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = a.ask("해지하면 이율이 어떻게 돼요?", "s")
    assert executed == ["중도해지 이율"] and res["model_calls"] == 3 and res["answer"] == "끝"


def test_chinese_search_query_that_survives_the_retry_is_never_run_the_code_searches_the_users_own_words_instead():
    executed = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="中途解约利率")), search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = a.ask("해지하면 이율이 어떻게 돼요?", "s")
    assert executed == ["해지하면 이율이 어떻게 돼요?"] and "中途解约利率" not in executed        # 중국어 검색어는 실행되지 않는다
    assert res["trace"][0]["forced"] is True and res["answer"] == g.UNSAFE_TEXT                       # 이후에도 모델이 중국어만 내면 고정 문구


def test_text_next_to_a_tool_call_is_not_checked_only_the_query():
    executed = []
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="중도해지"), text="好的") if n == 1 else AIMessage(content="끝"),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = a.ask("해지하면?", "s")
    assert executed == ["중도해지"] and res["tool_calls"] == 1 and res["model_calls"] == 2 and res["answer"] == "끝"       # 다시 시키지 않고 도구를 실행했다


def test_rules_tell_the_model_to_use_korean_only_and_not_to_invent_contacts():
    t = g.system_text({"today": "2025-03-04"})
    assert "한국어로만 답한다" in t and "상담원 연결 · 계좌 조회 · 해지 처리" in t and "전화번호나 주소는 도구 결과나 사용자가 말한 것만" in t


# ---------- 검증용 20문항의 실제 모델 실패를 그대로 재현 (수정 후 기대 동작) ----------
import sys as _sys  # noqa: E402

_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
HELD = {q["id"]: q["question"] for q in json.loads((Path(__file__).resolve().parents[1] / "eval" / "heldout" / "questions.json").read_text(encoding="utf-8"))["questions"]}
TODAY_H = "2026-10-28"


def ask_held(a, qid, session="h"):
    return a.ask(HELD[qid], session, today=TODAY_H)


def test_h1_the_maturity_date_the_model_forgot_is_filled_and_the_tool_answers_not_early_termination():
    """수정 전: 모델이 질문에 적힌 만기일을 안 넣어 만기일을 또 물었다."""
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=10_000_000, start_date="2025-06-01", termination_date="2026-10-28")) if n == 1
                 else AIMessage(content="해지일이 만기일 이후라 중도해지가 아니에요."))
    res = ask_held(a, "H1")
    result = last_tool_json(m.seen[1])
    assert result["status"] == "not_early_termination" and result["filled_args"] == {"만기일": "2026-06-01"}
    assert res["trace"][0]["filled"] == {"maturity_date": "2026-06-01"} and res["confirmed"]["maturity_date"] == "2026-06-01"


def test_h13_next_year_maturity_is_filled_and_the_base_rate_is_asked():
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=10_000_000, start_date="2026-04-02", termination_date="2026-10-15")) if n == 1
                 else AIMessage(content="신규일 당시 기본금리(연 %)는 얼마였나요?"))
    res = ask_held(a, "H13")
    assert last_tool_json(m.seen[1])["status"] == "need_base_rate" and res["trace"][0]["filled"] == {"maturity_date": "2027-04-02"}
    assert "기본금리" in res["answer"]


def test_h6_the_stated_maturity_is_filled_and_the_approximate_termination_is_asked_about():
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=7_000_000, start_date="2025-09-15", termination_date="2026-07-31")) if n == 1
                 else AIMessage(content="해지일을 날짜로 알려주세요."))
    res = ask_held(a, "H6")
    t = res["trace"][0]
    assert t["filled"] == {"maturity_date": "2026-09-15"} and t["dropped"] == {"termination_date": "2026-07-31"}      # '7월 말'은 정확한 날짜가 아니다
    assert last_tool_json(m.seen[1])["status"] == "missing_info"


def test_h2_the_wrong_relative_date_the_model_computed_is_replaced_by_the_codes_own_calculation():
    """수정 전: 모델이 '다음 달 20일'을 2026-04-20으로 계산했다 (맞는 값은 2026-11-20)."""
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=5_000_000, start_date="2026-03-10", termination_date="2026-04-20")) if n == 1
                 else AIMessage(content="만기일을 알려주세요."))
    res = ask_held(a, "H2")
    t = res["trace"][0]
    assert t["dropped"] == {"termination_date": "2026-04-20"} and t["filled"] == {"termination_date": "2026-11-20"}
    assert res["confirmed"]["termination_date"] == "2026-11-20"


def test_h3_h4_h20_invented_values_never_reach_the_model_again():
    """수정 전: 가드가 계산에서는 값을 뺐지만 모델의 호출 기록과 도구 결과에 지어낸 값이 남아, 답변에 '알려주신 원금은 2000만원, 신규일은 2026-05-28'이라고 썼다."""
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=20_000_000, start_date="2026-05-28", termination_date="2026-10-28")) if n == 1
                 else AIMessage(content="원금과 신규일, 만기일을 알려주세요."))
    res = ask_held(a, "H3")
    seen_by_model = json.dumps([[getattr(x, "content", ""), getattr(x, "tool_calls", [])] for x in m.seen[1]], ensure_ascii=False, default=str)
    assert "2026-05-28" not in seen_by_model and "20000000" not in seen_by_model                              # 두 번째 호출에서 모델이 본 것에 없다
    hist_call = next(x for x in a.history("h") if isinstance(x, AIMessage) and x.tool_calls)
    assert hist_call.tool_calls[0]["args"] == {"termination_date": "2026-10-28"} and hist_call.tool_calls[0]["id"]      # 호출 기록도 실제로 쓴 값으로 정리
    result = last_tool_json(m.seen[1])
    assert result["dropped_args"] == ["원금", "신규일"] and result["status"] == "missing_info"
    assert res["trace"][0]["dropped"] == {"principal": 20_000_000, "start_date": "2026-05-28"}                 # 디버깅용 기록에는 남는다


def test_an_answer_that_states_invented_values_is_retried_then_replaced_by_the_tools_own_question():
    bad = "알려주신 원금은 2000만원, 신규일은 2026-05-28입니다. 만기일을 알려주세요."

    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", principal=20_000_000, start_date="2026-05-28", termination_date="2026-10-28"))
        return AIMessage(content=bad if n == 2 else "원금과 신규일, 만기일을 알려주세요.")
    a, m = agent(fn)
    res = ask_held(a, "H3")
    assert res["answer"] == "원금과 신규일, 만기일을 알려주세요." and res["model_calls"] == 3
    assert "2026-05-28" in m.seen[2][-1].content and "20,000,000원" in m.seen[2][-1].content                  # 다시 시킬 때는 어떤 값이 문제인지 알려준다
    b, mb = agent(lambda msgs, n: ai(call("calc_interest", principal=20_000_000, start_date="2026-05-28", termination_date="2026-10-28")) if n == 1
                  else AIMessage(content=bad))
    res2 = ask_held(b, "H3")
    assert res2["answer"] == "계산에 필요한 값이 없습니다: 원금, 신규일, 만기일. 이 값을 알려주세요."              # 다시 시켜도 문제면 도구의 질문을 그대로


def test_h5_the_start_date_equal_to_today_and_the_made_up_maturity_do_not_produce_a_zero_interest_result():
    """수정 전 유일하게 가드가 계산을 막은 경우: 모델이 신규일 = 해지일 = 오늘로 지어내 '세전 이자 0원'이 나올 뻔했다."""
    a, m = agent(lambda msgs, n: ai(call("calc_interest", base_rate=3.2, maturity_date="2026-12-28", principal=15_000_000, start_date="2026-10-28",
                                         termination_date="2026-10-28")) if n == 1 else AIMessage(content="신규일은 언제인가요?"))
    res = ask_held(a, "H5")
    assert res["trace"][0]["status"] == "missing_info" and set(res["trace"][0]["dropped"]) == {"start_date", "maturity_date"}


def test_h8_a_word_the_user_never_said_in_a_date_field_is_dropped_not_shown_as_a_format_error():
    a, m = agent(lambda msgs, n: ai(call("calc_interest", base_rate=2.8, principal=30_000_000, termination_date="오늘")) if n == 1
                 else AIMessage(content="신규일, 만기일, 해지일을 알려주세요."))
    res = ask_held(a, "H8")
    assert res["trace"][0]["status"] == "missing_info" and res["trace"][0]["dropped"] == {"termination_date": "오늘"}
    assert "YYYY" not in json.dumps(last_tool_json(m.seen[1]), ensure_ascii=False)


def test_h14_tool_call_syntax_in_the_answer_is_retried_and_the_code_searches_first_if_it_stays():
    executed = []
    leak = '(calc_interest {"termination_date": "2026-10-28", "principal": 10000000})'
    a, m = agent(lambda msgs, n: AIMessage(content=leak) if n <= 2 else AIMessage(content="만기 후에는 약관에 따라 이자가 계산돼요."),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = ask_held(a, "H14")
    assert executed == [HELD["H14"]] and res["trace"][0]["forced"] is True and "calc_interest" not in res["answer"]
    assert res["answer"] == "만기 후에는 약관에 따라 이자가 계산돼요." and res["model_calls"] == 3
    assert "도구 호출 문법" in m.seen[1][-1].content


def test_h10_h11_a_not_found_claim_without_any_search_makes_the_code_search_first():
    """수정 전: 검색도 안 하고 '약관에서 확인되지 않습니다'라고 했다 (약관에는 일부해지 규칙이 있다)."""
    executed = []
    a, m = agent(lambda msgs, n: AIMessage(content="약관에서 확인되지 않습니다.") if n == 1 else AIMessage(content="일부해지는 약관에 따라 가능해요."),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = ask_held(a, "H10")
    assert executed == [HELD["H10"]] and res["answer"] == "일부해지는 약관에 따라 가능해요." and res["trace"][0]["forced"] is True


def test_not_found_is_allowed_when_the_search_really_found_nothing():
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="대출 한도")) if n == 1 else AIMessage(content="약관에서 확인되지 않습니다."),
                 search_fn=lambda q, **kw: {"status": "not_found", "query": q, "results": []})
    res = a.ask("예금 대출 한도가 얼마예요?", "s")
    assert res["answer"] == "약관에서 확인되지 않습니다." and res["model_calls"] == 2


def test_not_found_claim_when_the_search_found_results_is_retried_with_a_note():
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="일부 해지")) if n == 1
                 else AIMessage(content="약관에서 확인되지 않습니다." if n == 2 else "일부 해지는 약관에서 확인돼요."))
    res = a.ask("일부만 해지할 수 있어요?", "s")
    assert res["answer"] == "일부 해지는 약관에서 확인돼요." and "검색 결과가 있는데" in m.seen[2][-1].content


def test_small_talk_and_off_topic_questions_are_not_searched():
    executed = []
    for q in ("안녕하세요", "고마워요, 잘 알겠어요", "오늘 서울 날씨 어때요?"):
        a, m = agent(lambda msgs, n: AIMessage(content="네."), search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
        res = a.ask(q, "s")
        assert res["answer"] == "네." and res["tool_calls"] == 0
    assert executed == []


def test_a_domain_question_answered_without_tools_is_searched_first_and_the_forced_search_counts_toward_the_limit():
    executed = []
    a, m = agent(lambda msgs, n: AIMessage(content="약관상 가능해요.") if n == 1 else AIMessage(content="검색 결과에 따르면 가능해요."),
                 search_fn=lambda q, **kw: executed.append(q) or search_ok(q))
    res = a.ask("중도해지하면 이율이 어떻게 돼요?", "s")
    assert executed == ["중도해지하면 이율이 어떻게 돼요?"] and res["tool_calls"] == 1 and res["answer"] == "검색 결과에 따르면 가능해요."
    b, mb = agent(lambda msgs, n: AIMessage(content="약관상 가능해요."), max_tool_calls=0)
    assert b.ask("중도해지하면 이율이 어떻게 돼요?", "s")["stop_reason"] == "tool_limit"


def test_a_good_answer_after_a_real_calculation_passes_the_checks():
    q = "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요? 기본금리는 3.5%예요."
    good = "해지일 2026-12-07에 1000만원을 해지하면 세전 이자는 273,304원이에요."
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01",
                                         termination_date="2026-12-07", base_rate=3.5)) if n == 1 else AIMessage(content=good))
    res = a.ask(q, "s", today=TODAY_H)
    assert res["answer"] == good and res["model_calls"] == 2 and res["trace"][0]["status"] == "ok" and res["trace"][0]["dropped"] == {}


def test_an_interest_amount_the_model_computed_itself_is_blocked():
    q = "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요? 기본금리는 3.5%예요."
    a, m = agent(lambda msgs, n: ai(call("calc_interest", principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01",
                                         termination_date="2026-12-07", base_rate=3.5)) if n == 1 else AIMessage(content="세전 이자는 280,000원이에요."))
    res = a.ask(q, "s", today=TODAY_H)
    assert res["answer"] == g.UNSAFE_TEXT                                       # 도구가 준 273,304원이 아닌 금액은 말할 수 없다


# ---------- 점검 도우미 ----------
def _state(question, tool_results=(), confirmed=None, today=TODAY_H):
    msgs = [HumanMessage(question)]
    for i, (name, res) in enumerate(tool_results):
        msgs += [AIMessage(content="", tool_calls=[call(name)]), ToolMessage(content=json.dumps(res, ensure_ascii=False), tool_call_id=str(i), name=name)]
    return {"messages": msgs, "today": today, "confirmed": confirmed or {}}


def test_fallback_text_prefers_the_tools_own_question():
    st = _state("q", [("calc_interest", {"status": "need_base_rate", "message": "기본금리는 얼마였나요?"})])
    assert g.fallback_text(st) == "기본금리는 얼마였나요?"
    assert g.fallback_text(_state("q", [("calc_interest", {"status": "ok", "message": "x"})])) == g.UNSAFE_TEXT and g.fallback_text(_state("q")) == g.UNSAFE_TEXT


@pytest.mark.parametrize("question,reply,tools,expected", [
    ("중도해지하면?", AIMessage(content="답"), [], True), ("안녕하세요", AIMessage(content="안녕하세요"), [], False),
    ("안녕하세요", AIMessage(content="약관에서 확인되지 않습니다."), [], True),                                       # 도메인 낱말이 없어도 '확인되지 않는다'는 말이면 검색이 먼저
    ("중도해지하면?", AIMessage(content="답"), [("search_terms", {"status": "ok"})], False),                        # 이미 도구를 불렀다
    ("중도해지하면?", ai(call("search_terms", query="x")), [], False),                                              # 도구를 부르는 중이다
])
def test_needs_forced_search(question, reply, tools, expected):
    assert g.needs_forced_search(reply, _state(question, tools)) is expected


@pytest.mark.parametrize("reply,tools,confirmed,problem", [
    ("세전 이자는 273,304원이에요.", [("calc_interest", {"interest_krw": 273304})], {}, None),
    ("세전 이자는 273,304원이에요.", [], {}, "ungrounded"),
    ("신규일은 2026-01-01이에요.", [], {"start_date": "2026-01-01"}, None),                                         # 확인된 값은 허용
    ("(calc_interest {})", [], {}, "syntax"), ("利息", [], {}, "language"), ("고객센터 1544-1111", [], {}, "phone"),
    ("약관에서 확인되지 않습니다.", [("search_terms", {"status": "ok"})], {}, "not_found"),
    ("약관에서 확인되지 않습니다.", [("search_terms", {"status": "not_found"})], {}, None),
])
def test_check_reply_problems(reply, tools, confirmed, problem):
    assert g.check_reply(AIMessage(content=reply), _state("1000만원 넣었어요", tools, confirmed))[0] == problem


def test_amounts_from_an_earlier_turns_tool_result_are_allowed_but_phone_numbers_are_not():
    st = {"messages": [HumanMessage("첫 질문"), ToolMessage(content='{"interest_krw": 273304, "text": "고객센터 1599-1111"}', tool_call_id="1", name="calc_interest"),
                       AIMessage(content="앞 답"), HumanMessage("방금 계산한 이자가 얼마였죠?")], "today": TODAY_H}
    assert g.check_reply(AIMessage(content="273,304원이었어요."), st)[0] is None
    assert g.check_reply(AIMessage(content="고객센터 1599-1111로 문의하세요."), st)[0] == "phone"
