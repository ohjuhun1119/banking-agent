"""상담 요청 흐름(8일차) 그래프 시험: 저장은 사용자가 직접 요청했거나 '네'라고 확인한 턴에만, 접수번호는 저장에 성공한 뒤에만 나온다."""
import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_graph as tg  # noqa: E402
from app import graph as g  # noqa: E402
from app.tools import handoff as hf  # noqa: E402

agent, ai, call = tg.agent, tg.ai, tg.call
ASK = "사람이랑 얘기하고 싶어요"                     # 키워드(상담원 · 직원 연결 등)에 걸리지 않는 표현


def saved_messages():
    import sqlite3
    con = sqlite3.connect(hf.DB_PATH)
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT messages FROM handoff_requests ORDER BY id")]
    finally:
        con.close()


def model_asks_to_confirm(msgs, n):
    return ai(call("request_handoff")) if n == 1 else AIMessage(content=g.HANDOFF_CONFIRM_TEXT)


# ---------- 키워드로 직접 요청: 바로 저장 ----------
def test_a_keyword_request_saves_the_previous_question_and_the_confirmed_values_not_the_keyword_message():
    a, m = agent(tg.calc_all, search_fn=tg.rule_search())
    a.ask(tg.CALC_Q, "s", today=tg.TODAY_H)
    res = a.ask("상담원 연결해줘", "s")
    assert res["stop_reason"] == "handoff_saved" and res["model_calls"] == 0 and "접수번호는" in res["answer"]
    assert saved_messages() == [[tg.CALC_Q]]                                       # '상담원 연결해줘'만으로는 요약이 안 되므로 빠진다
    import sqlite3
    confirmed = sqlite3.connect(hf.DB_PATH).execute("SELECT confirmed FROM handoff_requests").fetchone()[0]
    assert "10,000,000" in confirmed and res["answer"].count("20") >= 1             # 확인된 값이 같이 저장되고, 접수번호(날짜-번호)가 답에 있다


def test_a_keyword_request_with_nothing_else_still_saves_something():
    a, m = agent(lambda msgs, n: AIMessage(content="x"))
    res = a.ask("상담원 연결해줘", "s")
    assert res["stop_reason"] == "handoff_saved" and saved_messages() == [["상담원 연결해줘"]]


def test_a_failed_save_never_says_it_was_received(tmp_path, monkeypatch):
    broken = tmp_path / "folder"
    broken.mkdir()
    monkeypatch.setattr(hf, "DB_PATH", broken)                                     # 폴더라 저장이 실패한다
    a, m = agent(lambda msgs, n: AIMessage(content="x"))
    res = a.ask("상담원 연결해줘", "s")
    assert res["answer"] == g.HANDOFF_FAIL_TEXT and res["stop_reason"] == "handoff_failed" and "접수번호" not in res["answer"]
    assert "접수되지 않았으니" in res["answer"] and res["trace"][0]["status"] == "error"


def test_an_empty_request_is_reported_without_a_ticket(monkeypatch):
    monkeypatch.setattr(hf, "request_handoff", lambda *a, **k: {"status": "invalid_input", "message": "x"})
    a, m = agent(lambda msgs, n: AIMessage(content="x"))
    res = a.ask("상담원 연결해줘", "s")
    assert res["answer"] == g.HANDOFF_EMPTY_TEXT and res["stop_reason"] == "handoff_failed" and "접수번호" not in res["answer"]


# ---------- 모델이 request_handoff를 부르면: 저장하지 않고 묻는다 -> '네' -> 저장 ----------
def test_when_the_model_calls_the_tool_nothing_is_saved_and_the_user_is_asked_first():
    a, m = agent(model_asks_to_confirm)
    res = a.ask(ASK, "s")
    t = res["trace"][0]
    assert t["name"] == "request_handoff" and t["status"] == "needs_confirmation" and t["forced"] is False
    assert g.HANDOFF_CONFIRM_MARK in res["answer"] and res["stop_reason"] is None and hf.count_requests() == 0
    assert res["evidence"] == []


def test_yes_after_the_confirmation_question_saves_the_original_question_without_calling_the_model():
    a, m = agent(model_asks_to_confirm)
    a.ask(ASK, "s")
    calls_before = m.n
    res = a.ask("네, 저장해 주세요", "s")
    assert res["stop_reason"] == "handoff_saved" and res["model_calls"] == 0 and m.n == calls_before
    assert saved_messages() == [[ASK]]                                             # 확인 답('네')은 요약에 넣지 않는다


@pytest.mark.parametrize("reply", ["아니요 괜찮아요", "됐어요", "네 그런데 다른 건요?"])
def test_anything_but_a_plain_yes_goes_to_the_model_and_saves_nothing(reply):
    a, m = agent(lambda msgs, n: model_asks_to_confirm(msgs, n) if n <= 2 else AIMessage(content="알겠어요."))
    a.ask(ASK, "s")
    res = a.ask(reply, "s")
    assert res["stop_reason"] is None and res["model_calls"] == 1 and hf.count_requests() == 0


def test_yes_without_a_preceding_question_is_just_a_message():
    a, m = agent(lambda msgs, n: AIMessage(content="안녕하세요!") if n == 1 else AIMessage(content="네."))
    a.ask("안녕하세요", "s")
    res = a.ask("네", "s")
    assert res["stop_reason"] is None and res["model_calls"] == 1 and hf.count_requests() == 0


def test_yes_two_turns_after_the_question_does_not_save():
    a, m = agent(lambda msgs, n: model_asks_to_confirm(msgs, n) if n <= 2 else AIMessage(content="천만에요."))
    a.ask(ASK, "s")
    a.ask("고마워요", "s")                                                          # 직전 답변이 확인 질문이 아니게 된다
    res = a.ask("네", "s")
    assert res["stop_reason"] is None and hf.count_requests() == 0


# ---------- 모델이 지시를 어기면 ----------
def test_a_model_that_claims_the_request_was_received_is_retried_and_then_replaced_by_the_confirmation_question():
    a, m = agent(lambda msgs, n: ai(call("request_handoff")) if n == 1 else AIMessage(content="상담 요청을 접수했어요."))
    res = a.ask(ASK, "s")
    assert res["answer"] == g.HANDOFF_CONFIRM_TEXT and res["model_calls"] == 3 and hf.count_requests() == 0
    assert "접수번호도 시스템만" in m.seen[2][-1].content
    b, mb = agent(lambda msgs, n: ai(call("request_handoff")) if n == 1 else AIMessage(content="상담 요청을 접수했어요." if n == 2 else g.HANDOFF_CONFIRM_TEXT))
    assert b.ask(ASK, "s")["answer"] == g.HANDOFF_CONFIRM_TEXT                       # 다시 시키면 고친다


def test_a_model_that_forgets_to_ask_is_made_to_ask_so_that_yes_can_be_understood_next_turn():
    a, m = agent(lambda msgs, n: ai(call("request_handoff")) if n == 1 else AIMessage(content="알겠습니다."))
    res = a.ask(ASK, "s")
    assert res["answer"] == g.HANDOFF_CONFIRM_TEXT and "저장할지 물어본다" in m.seen[2][-1].content
    assert a.ask("네", "s")["stop_reason"] == "handoff_saved"


def test_a_connection_promise_without_calling_the_tool_is_replaced_and_nothing_is_saved():
    a, m = agent(lambda msgs, n: AIMessage(content="상담원이 연결해 드릴게요."))
    res = a.ask(ASK, "s")
    assert res["answer"] == g.NO_CONNECT_TEXT and "직접 해 드릴 수 없어요" in res["answer"] and hf.count_requests() == 0
    assert res["evidence"] == []


# ---------- 한도 · 도구 오류 뒤에도 '네'로 저장 ----------
def test_after_the_tool_limit_message_yes_saves_the_request():
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="a"), call("search_terms", query="b")), search_fn=tg.rule_search(), max_tool_calls=1)
    res = a.ask("중도해지하면 이율이 어떻게 돼요?", "s")
    assert res["stop_reason"] == "tool_limit" and g.HANDOFF_CONFIRM_MARK in res["answer"]
    saved = a.ask("네", "s")
    assert saved["stop_reason"] == "handoff_saved" and saved_messages() == [["중도해지하면 이율이 어떻게 돼요?"]]


def test_a_tool_that_keeps_failing_gets_one_retry_and_then_an_honest_failure_message_with_the_offer():
    searched = []

    def failing(q, **kw):
        searched.append(q)
        raise RuntimeError("색인이 열리지 않음")
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="x")) if n == 1 else AIMessage(content="음, 잘 모르겠어요."), search_fn=failing)
    res = a.ask("일부 해지가 되나요?", "s")
    assert len(searched) == 2 and res["trace"][0]["retried"] is True and res["trace"][0]["status"] == "error"       # 1회 재시도
    assert res["answer"] == g.TOOL_ERROR_TEXT and "다시 시도" in res["answer"] and g.HANDOFF_CONFIRM_MARK in res["answer"] and res["evidence"] == []
    assert a.ask("네", "s")["stop_reason"] == "handoff_saved"


def test_a_failure_the_model_already_reported_is_left_alone():
    ok = "검색에 실패했어요. 잠시 후 다시 시도해 주세요."
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="x")) if n == 1 else AIMessage(content=ok),
                 search_fn=lambda q, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert a.ask("일부 해지가 되나요?", "s")["answer"] == ok


# ---------- 세션 분리 ----------
def test_a_request_only_contains_the_questions_of_its_own_session():
    a, m = agent(lambda msgs, n: AIMessage(content="답"))
    a.ask("A 세션의 질문입니다", "sA")
    a.ask("B 세션의 질문입니다", "sB")
    a.ask("상담원 연결해줘", "sB")
    assert saved_messages() == [["B 세션의 질문입니다"]]


# ---------- 흐름 점검 ----------
def test_the_handoff_node_is_reached_only_through_the_entry_check():
    from langchain_core.messages import HumanMessage
    assert g.handoff_intent([HumanMessage("상담원 연결해줘")])
    assert not g.handoff_intent([HumanMessage("안녕하세요")]) and not g.handoff_intent([])
    assert not g.handoff_intent([HumanMessage("네")])
    assert g.handoff_intent([HumanMessage("질문"), AIMessage(content=g.HANDOFF_CONFIRM_TEXT), HumanMessage("네")])
    assert not g.handoff_intent([HumanMessage("질문"), AIMessage(content="다른 답"), HumanMessage("네")])


def test_the_model_is_given_the_third_tool_but_the_measured_two_tool_definitions_are_untouched():
    from app import tool_calling as tc
    assert [t["function"]["name"] for t in tc.tool_specs()] == ["search_terms", "calc_interest"]                 # 5일차 측정 대상은 그대로
    assert tc.HANDOFF_SPEC["function"]["name"] == "request_handoff" and tc.HANDOFF_SPEC["function"]["parameters"]["required"] == []
    assert "저장하지 않는다" in tc.HANDOFF_SPEC["function"]["description"]
