"""옵션 B(9일차 재실행 후): 코드가 먼저 부른 계산이 값을 되물을 때는 도구의 질문을 그대로 답한다 (모델이 문장을 다시 쓰다 뜻을 뒤집은 H19)."""
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_graph as tg  # noqa: E402
from app import graph as g  # noqa: E402
from app.tools.interest import calc_interest  # noqa: E402

agent, ai, call, last_tool_json = tg.agent, tg.ai, tg.call, tg.last_tool_json
TODAY = tg.TODAY_H                                                       # 2026-10-28
NO_TOOL = AIMessage(content="이자는 계산해 봐야 알 수 있어요.")


def once_then_fail(msgs, n):
    """모델은 처음 한 번만 부를 수 있다 (도구 없이 답함). 다시 부르면 시험이 실패한다."""
    if n == 1:
        return NO_TOOL
    raise AssertionError("값을 되묻는 문장에는 모델을 다시 부르면 안 된다")


def test_a_forced_calculation_that_asks_for_values_answers_with_the_tool_message_and_no_second_model_call():
    a, m = agent(once_then_fail)
    res = a.ask("정기예금 만기 전에 해지하면 이자 얼마 받아요?", "s", today=TODAY)
    t = res["trace"][0]
    assert t["forced"] and t["forced_kind"] == "calc" and t["status"] == "missing_info"
    assert res["answer"] == calc_interest()["message"] == "계산에 필요한 값이 없습니다: 원금, 신규일, 만기일, 해지일. 이 값을 알려주세요."      # 날짜가 없으니 채운 값도 없다
    assert m.n == 1 and res["model_calls"] == 1 and res["error"] is None and res["stop_reason"] is None and res["evidence"] == []


def test_the_values_the_code_filled_from_the_question_are_shown_so_the_assumption_is_visible():
    a, m = agent(once_then_fail)
    res = a.ask("지금 해지하면 이자 얼마 받아요?", "s", today=TODAY)
    assert res["answer"].endswith("(질문에서 읽은 값: 해지일 2026-10-28)")
    b, mb = agent(once_then_fail)
    assert "질문에서 읽은 값" not in b.ask("만기 전에 해지하면 이자 얼마 받아요?", "s", today=TODAY)["answer"]        # 채운 값이 없으면 덧붙이지 않는다


def test_when_everything_is_given_but_the_base_rate_the_rate_question_is_verbatim_with_the_filled_values_and_the_rule_card():
    a, m = agent(once_then_fail, search_fn=tg.rule_search())
    res = a.ask("1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요?", "s", today=TODAY)
    expected = calc_interest(principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-12-07")["message"]
    assert res["trace"][0]["status"] == "need_base_rate" and res["answer"].startswith(expected) and m.n == 1
    for part in ("원금 10,000,000원", "신규일 2026-01-01", "만기일 2027-01-01", "해지일 2026-12-07"):
        assert part in res["answer"]
    assert [c["kind"] for c in res["evidence"]] == ["약관"]                                  # 근거 카드는 그대로 붙는다


def test_a_forced_calculation_that_finishes_is_still_explained_by_the_model():
    def fn(msgs, n):
        if n == 1:
            return NO_TOOL
        return AIMessage(content=f"세전 이자는 {last_tool_json(msgs)['interest_krw']:,}원이에요.")
    a, m = agent(fn, search_fn=tg.rule_search())
    res = a.ask("1000만원을 2026-10-01에 넣었고 만기는 2027-10-01이에요. 2026-10-20에 해지하면 이자 얼마예요?", "s", today=TODAY)
    assert res["trace"][0]["forced"] and res["trace"][0]["status"] == "ok" and res["model_calls"] == 2 and res["answer"].startswith("세전 이자는")


def test_a_calculation_the_model_chose_itself_still_goes_through_the_model():
    a, m = agent(lambda msgs, n: ai(call("calc_interest")) if n == 1 else AIMessage(content="원금과 신규일, 만기일, 해지일을 알려주세요."))
    res = a.ask("이자 얼마 받아요?", "s", today=TODAY)
    assert res["trace"][0]["forced"] is False and res["model_calls"] == 2 and res["answer"] == "원금과 신규일, 만기일, 해지일을 알려주세요."


def test_the_model_reversing_the_meaning_can_no_longer_reach_the_user():
    """검증용 H19: 도구는 '값이 없습니다'라고 했는데 모델이 '값이 있습니다'로 다시 썼다. 이제 그 문장은 모델이 쓰지 않는다."""
    a, m = agent(lambda msgs, n: NO_TOOL if n == 1 else AIMessage(content="계산에 필요한 값이 있습니다: 원금, 신규일, 만기일. 이 값을 알려주세요."))
    res = a.ask("만기 한 달 전인데 지금 해지하는 거랑 만기까지 기다리는 거랑 이자 차이가 많이 나나요?", "s", today=TODAY)
    assert "필요한 값이 없습니다" in res["answer"] and "필요한 값이 있습니다" not in res["answer"] and res["model_calls"] == 1


# ---------- 함수 단위 ----------
def state_with(result: dict, trace_entry: dict, last_is_tool=True):
    import json
    msgs = [HumanMessage("질문")]
    if last_is_tool:
        msgs.append(ToolMessage(content=json.dumps(result, ensure_ascii=False), name="calc_interest", tool_call_id="c"))
    return {"messages": msgs, "trace": [trace_entry]}


FORCED = {"name": "calc_interest", "forced": True, "forced_kind": "calc", "status": "missing_info"}


def test_verbatim_applies_only_to_forced_calculations_that_ask():
    r = {"status": "missing_info", "message": "값을 알려주세요."}
    assert g.verbatim_question(state_with(r, FORCED)) == "값을 알려주세요."
    assert g.verbatim_question(state_with({**r, "status": "need_base_rate"}, {**FORCED, "status": "need_base_rate"})) == "값을 알려주세요."
    for status in ("ok", "invalid_input", "not_early_termination", "error", "duplicate"):
        assert g.verbatim_question(state_with({**r, "status": status}, {**FORCED, "status": status})) is None
    assert g.verbatim_question(state_with(r, {**FORCED, "forced": False})) is None                       # 모델이 직접 부른 계산
    assert g.verbatim_question(state_with(r, {**FORCED, "forced_kind": "search"})) is None               # 강제 검색
    assert g.verbatim_question(state_with(r, {**FORCED, "forced_kind": "handoff"})) is None
    assert g.verbatim_question(state_with(r, FORCED, last_is_tool=False)) is None                         # 도구 결과가 방금 나온 것이 아님
    assert g.verbatim_question({"messages": [], "trace": []}) is None and g.verbatim_question({"messages": [HumanMessage("q")]}) is None


@pytest.mark.parametrize("message", ["", "   ", None, 5])
def test_an_empty_or_missing_tool_message_falls_back_to_the_model(message):
    assert g.verbatim_question(state_with({"status": "missing_info", "message": message}, FORCED)) is None


def test_filled_values_are_formatted_for_reading():
    r = {"status": "need_base_rate", "message": "기본금리는요?", "filled_args": {"원금": 10_000_000, "신규일": "2026-01-01", "기본금리": 3.5}}
    assert g.verbatim_question(state_with(r, {**FORCED, "status": "need_base_rate"})) == "기본금리는요? (질문에서 읽은 값: 원금 10,000,000원, 신규일 2026-01-01, 기본금리 연 3.5%)"
    assert g._filled_text({}) == ""


def test_the_trace_marks_the_forced_calculation_and_the_chat_response_reports_one_model_call():
    a, m = agent(once_then_fail)
    res = a.ask("정기예금 만기 전에 해지하면 이자 얼마 받아요?", "s", today=TODAY)
    assert [t["forced_kind"] for t in res["trace"]] == ["calc"] and res["model_calls"] == 1 and res["tool_calls"] == 1


def test_a_tool_result_that_is_no_longer_the_last_message_is_not_answered_verbatim():
    """도구 결과 뒤에 이미 다른 메시지(답변)가 있으면 같은 질문을 다시 내보내지 않는다."""
    s = state_with({"status": "missing_info", "message": "값을 알려주세요."}, FORCED)
    assert g.verbatim_question(s) == "값을 알려주세요."
    s["messages"].append(AIMessage(content="이미 답함"))
    assert g.verbatim_question(s) is None


# ---------- 세후 · 세금을 물었는데 값을 되묻기만 하면 세후 안내가 사라진다 (개발용 Q045 · Q046) ----------
TAX = "세후(세금을 뺀) 금액은 계산하지 않고 세전 이자만 안내해요."


@pytest.mark.parametrize("question", ["세후 이자는 얼마예요?", "세금 떼고 실제로 얼마 받아요?", "원천징수하고 나면 이자 얼마예요?", "실수령 이자가 얼마나 돼요?"])
def test_a_tax_question_that_is_answered_by_asking_for_values_also_says_after_tax_is_not_calculated(question):
    a, m = agent(once_then_fail)
    res = a.ask(question, "s", today=TODAY)
    assert res["answer"] == calc_interest()["message"] + " " + TAX and m.n == 1 and res["trace"][0]["forced_kind"] == "calc"


def test_the_tax_notice_comes_after_the_values_the_code_read_and_only_when_the_question_mentions_tax():
    a, m = agent(once_then_fail)
    res = a.ask("지금 해지하면 세금 떼고 이자 얼마 받아요?", "s", today=TODAY)
    assert res["answer"].endswith("(질문에서 읽은 값: 해지일 2026-10-28) " + TAX)
    b, mb = agent(once_then_fail)
    assert TAX not in b.ask("지금 해지하면 이자 얼마 받아요?", "s", today=TODAY)["answer"]


def test_the_tax_notice_is_also_added_when_only_the_base_rate_is_missing():
    a, m = agent(once_then_fail, search_fn=tg.rule_search())
    res = a.ask("1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 세후 이자 얼마예요?", "s", today=TODAY)
    assert res["trace"][0]["status"] == "need_base_rate" and res["answer"].endswith(TAX)


def test_the_tax_notice_does_not_touch_answers_the_model_writes_or_calculations_that_finish():
    a, m = agent(lambda msgs, n: ai(call("calc_interest")) if n == 1 else AIMessage(content="원금과 날짜를 알려주세요."))
    assert a.ask("세후 이자 얼마 받아요?", "s", today=TODAY)["answer"] == "원금과 날짜를 알려주세요."          # 모델이 직접 부른 계산: 모델이 답한다


@pytest.mark.parametrize("qid", ["Q045", "Q046"])
def test_the_dev_after_tax_questions_now_pass_the_grader_end_to_end(qid):
    """개발용 세후 문항이 실제로 통과한다: 계산형으로 판별 → 강제 계산 호출 → 값 되묻기 + 세후 안내 → 채점기 통과 (가짜 모델, 실제 문항 · 정답)."""
    import json
    from pathlib import Path
    from eval import evaluate as ev
    row = next(r for r in map(json.loads, (Path(__file__).resolve().parents[1] / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines()) if r["id"] == qid)
    a, m = agent(once_then_fail)
    res = a.ask(row["question"], "s", today=row["today"])
    g = ev.grade(row, res)
    assert TAX in res["answer"] and g["behavior_ok"] and g["clean"] and g["flags"] == [], (g, res["answer"])


def test_the_tax_notice_follows_this_turns_question_not_an_earlier_one():
    """앞 질문이 세금 얘기였어도 이번 질문에 세금이 없으면 세후 안내를 붙이지 않는다."""
    def fn(msgs, n):
        if n == 1:
            return AIMessage(content="약관상 붙어요.")
        if n == 2:
            return AIMessage(content="검색 결과에 따르면 그래요.")
        if n == 3:
            return AIMessage(content="계산해 봐야 해요.")
        raise AssertionError("값을 되묻는 문장에는 모델을 다시 부르면 안 된다")
    a, m = agent(fn, search_fn=tg.rule_search())
    first = a.ask("예금 이자에 세금이 붙나요?", "s", today=TODAY)
    assert first["trace"][0]["name"] == "search_terms" and TAX not in first["answer"]
    second = a.ask("지금 해지하면 이자 얼마 받아요?", "s", today=TODAY)
    assert second["trace"][0]["forced_kind"] == "calc" and TAX not in second["answer"] and "세후" not in second["answer"]
    third = a.ask("지금 해지하면 세후 이자 얼마 받아요?", "s", today=TODAY) if False else None
