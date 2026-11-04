"""중도해지금리 구간 조회 (튜터 피드백: 표를 잘못 읽던 문제). 개발용 Q023: 가입한 지 두 달인데 7B 모델이 '3개월 이상 ~ 6개월 미만'이라고 답했다.
구간을 찾는 일을 코드가 맡는다: (1) 도구, (2) 질문에서 경과 기간 읽기, (3) 그래프에서 모델 없이 답하기, (4) 근거 카드, (5) 채점기와 데이터셋."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_graph as tg  # noqa: E402
from app import elapsed_period as ep  # noqa: E402
from app import graph as g  # noqa: E402
from app import tool_calling as tc  # noqa: E402
from app.evidence import build_evidence  # noqa: E402
from app.tools import interest as it  # noqa: E402
from app.tools.rate_lookup import lookup_termination_rate as lookup  # noqa: E402
from eval import evaluate as ev  # noqa: E402

TODAY = tg.TODAY_H
Q023 = "가입한 지 두 달 됐는데 지금 해지하면 금리가 얼마예요?"


def never(msgs, n):
    raise AssertionError("구간 조회에는 모델을 부르면 안 된다")


RULE_SRC = {"doc_title": "『하나의 정기예금』 요약 상품설명서", "section": "거래 조건 > 중도해지금리", "review_no": "제2025-설명서-199호", "as_of": "2025-09-01"}


def early_search(calls=None):
    def fn(q, **kw):
        if calls is not None:
            calls.append(q)
        return {"status": "ok", "query": q, "results": [{"rank": 1, "chunk_id": "hana-deposit-sheet__early_termination", "chunk_type": "중도해지", "source": RULE_SRC, "text": "본문", "distance": 0.1}]}
    return fn


# ---------- 1. 도구
@pytest.mark.parametrize("months,label,rate", [(0, "1개월 미만", "0.100"), (1, "1개월 이상 ~ 3개월 미만", "0.150"), (2, "1개월 이상 ~ 3개월 미만", "0.150"),
                                                (3, "3개월 이상 ~ 6개월 미만", "0.200"), (5, "3개월 이상 ~ 6개월 미만", "0.200")])
def test_below_six_months_the_fixed_rate_is_given(months, label, rate):
    r = lookup(months)
    assert r["status"] == "ok" and r["kind"] == "fixed" and r["section"] == label and r["rate_pct"] == rate
    assert f"연 {float(rate):.2f}%(고정)" in r["message"] and f"'{label}'" in r["message"]


@pytest.mark.parametrize("months,label,pct", [(6, "6개월 이상 ~ 9개월 미만", 60), (8, "6개월 이상 ~ 9개월 미만", 60), (9, "9개월 이상 ~ 11개월 미만", 70),
                                               (10, "9개월 이상 ~ 11개월 미만", 70), (11, "11개월 이상", 90), (60, "11개월 이상", 90)])
def test_from_six_months_the_rule_is_given_not_a_made_up_rate(months, label, pct):
    r = lookup(months)
    assert r["status"] == "ok" and r["kind"] == "differential" and r["section"] == label and r["diff_pct"] == pct and "rate_pct" not in r
    assert f"기본금리의 {pct}%" in r["message"] and "연 0.20%" in r["message"] and "기본금리" in r["message"]          # 금리를 지어내지 않고 기본금리를 요구한다


def test_every_month_uses_the_same_section_names_as_the_calculation_tool():
    """구간표는 한 곳에만 있다: 조회 도구와 계산 도구가 어긋나면 이 시험이 깨진다."""
    for n in range(0, 61):
        assert lookup(n)["section"] == it.section_label(n)


@pytest.mark.parametrize("value,status", [(None, "missing_info"), (-1, "invalid_input"), (61, "invalid_input"), ("abc", "invalid_input"), (2.5, "invalid_input"), (True, "invalid_input"), ([], "invalid_input")])
def test_bad_or_missing_months_are_reported_not_guessed(value, status):
    r = lookup(value)
    assert r["status"] == status and "section" not in r and r["message"]


def test_digit_strings_and_whole_floats_are_accepted_and_the_result_is_json_serializable():
    assert lookup("2")["section"] == lookup(2.0)["section"] == "1개월 이상 ~ 3개월 미만"
    json.dumps([lookup(n) for n in (0, 2, 8, 60)] + [lookup(None), lookup(99)], ensure_ascii=False)


def test_the_tool_is_hidden_from_the_model_and_run_tool_checks_its_arguments():
    assert tc.LOOKUP_NAME not in tc.TOOL_NAMES and tc.LOOKUP_NAME in tc.CODE_ONLY_TOOLS
    assert [s["function"]["name"] if "function" in s else s["name"] for s in tc.tool_specs()] == list(tc.TOOL_NAMES)          # 모델에게 보이는 도구는 그대로다
    assert tc.run_tool(tc.LOOKUP_NAME, {"elapsed_months": 2})["status"] == "ok"
    assert tc.run_tool(tc.LOOKUP_NAME, {"months": 2})["status"] == "invalid_input"


# ---------- 2. 질문에서 경과 기간 읽기
@pytest.mark.parametrize("question,months", [
    (Q023, 2), ("가입한 지 6개월 지났는데 해지하면 금리가 어떻게 돼요?", 6), ("넣은 지 석 달 정도 됐는데 지금 해지하면 금리가 어떻게 돼요?", 3),
    ("예금 든 지 1년 됐어요. 지금 깨면 이율이 얼마예요?", 12), ("가입한 지 한 달 됐는데 중도해지하면 이율이 몇 퍼센트예요?", 1),
    ("가입한 지 열두 달 됐는데 해지하면 금리가 궁금해요", 12), ("가입한 지 여덟 달 됐는데 해지하면 이율은요?", 8),
    ("두 달 전에 가입했는데 지금 해지하면 금리가 어떻게 돼요?", 2), ("1년 3개월 전에 가입했는데 해지하면 이율이 몇 퍼센트예요?", 15),
    ("가입한 지 반년 됐는데 중도해지 금리가 어떻게 돼요?", 6), ("가입한 지 1년 반 됐는데 해지하면 금리가 얼마예요?", 18),
    ("가입한 지 약 두 달 됐는데 중도해지금리가 어떻게 돼요?", 2), ("가입한 지 2달 됐는데 해약하면 금리가 어떻게 돼요?", 2),
    ("가입한 지 5년 됐는데 해지하면 금리가 얼마예요?", 60),
])
def test_elapsed_periods_are_read_from_how_people_really_say_it(question, months):
    assert ep.elapsed_months(question) == months
    assert ep.lookup_args(question) == {"elapsed_months": months}


@pytest.mark.parametrize("question,why", [
    ("만기까지 두 달 남았는데 지금 해지하면 이자 거의 못 받나?", "남은 기간(검증용 X014)은 경과 기간이 아니다"),
    ("6개월 안에 해지하면 이자를 거의 못 받나요?", "일반적인 규칙 질문(Q027)이다"),
    ("정기예금 중도해지하면 이율이 어떻게 돼요?", "경과 기간을 말하지 않았다"),
    ("가입한 지 두 달 됐는데 지금 해지하면 이자 얼마예요?", "이자 금액을 묻는 질문은 계산 도구의 몫이다"),
    ("가입한 지 두 달 됐는데 금리가 얼마예요?", "해지를 말하지 않았다 (기본금리를 묻는 말일 수 있다)"),
    ("가입한 지 두 달 됐는데 다음 달에 해지하면 금리가 얼마예요?", "지금 시점의 질문이 아니다"),
    ("가입한 지 두 달 됐는데 6월 15일에 해지하면 금리가 얼마예요?", "해지 날짜를 말했다"),
    ("가입한 지 두 달 됐는데 한 달 뒤에 해지하면 금리가 얼마예요?", "한 달 뒤는 지금이 아니다"),
    ("가입한 지 2일 됐는데 해지하면 금리가 얼마예요?", "일 단위는 읽지 않는다"),
    ("가입한 지 100개월 됐는데 해지하면 금리가 얼마예요?", "계약기간 최대 5년을 넘는다"),
    ("가입한 지 70개월 됐는데 해지하면 금리가 얼마예요?", "60개월 상한 바로 위도 읽지 않는다"),
    ("가입한 지 5년 1개월 됐는데 해지하면 금리가 얼마예요?", "61개월은 상한을 넘는다"),
    ("가입한 지 두 달 됐는데 지금 해지하면 금리 적용해서 이자 얼마예요?", "금리 낱말이 있어도 이자 금액을 묻는 질문은 계산 도구의 몫이다"),
    ("가입한 지 두 달이 안 됐는데 해지하면 금리가 얼마예요?", "정확한 개월 수가 아니다"),
    ("만기 6개월 전에 해지하면 금리가 어떻게 돼요?", "가입한 지 얼마가 아니다"),
    ("대출 금리가 얼마예요?", "중도해지 질문이 아니다"),
])
def test_questions_that_are_not_an_elapsed_period_are_left_alone(question, why):
    assert ep.lookup_args(question) is None, why


def test_non_strings_are_ignored():
    assert ep.lookup_args(None) is None and ep.lookup_args(123) is None and ep.elapsed_months(None) is None


def test_among_all_115_evaluation_questions_only_q023_triggers_the_lookup():
    """낱말 규칙이 다른 문항을 건드리지 않는다는 것을 데이터셋 전체로 고정한다 (검증용 31문항 포함: X014도 읽히지 않는다)."""
    rows = [json.loads(line) for line in (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 115
    assert [(r["id"], r["split"]) for r in rows if ep.lookup_args(r["question"])] == [("Q023", "dev")]


# ---------- 3. 그래프: 모델 없이 코드가 구간을 찾아 답한다
def ask(question, search_fn=None, session="s"):
    a, m = tg.agent(never, search_fn=search_fn or early_search())
    return a.ask(question, session, today=TODAY), m


def test_the_question_is_answered_by_the_code_without_calling_the_model():
    res, m = ask(Q023)
    assert m.n == 0 and res["model_calls"] == 0 and res["stop_reason"] is None and res["error"] is None
    assert "'1개월 이상 ~ 3개월 미만'" in res["answer"] and "연 0.15%(고정)" in res["answer"]
    assert "3개월 이상 ~ 6개월 미만" not in res["answer"] and "6개월 이상" not in res["answer"]            # 개발용 Q023의 오답이 나오지 않는다
    assert res["answer"] == lookup(2)["message"]                                                           # 도구의 문장 그대로


def test_the_lookup_and_the_search_for_the_evidence_are_both_traced_as_code_forced_calls():
    res, _ = ask(Q023)
    assert [(t["name"], t["status"], t["forced"], t["forced_kind"]) for t in res["trace"]] == [("lookup_termination_rate", "ok", True, "lookup"), ("search_terms", "ok", True, "lookup")]
    assert res["trace"][0]["args"] == {"elapsed_months": 2} and res["tool_calls"] == 2


def test_the_search_is_for_the_evidence_card_and_uses_the_question_as_the_query():
    calls = []
    res, _ = ask(Q023, early_search(calls))
    assert calls == [Q023]
    assert [(e["kind"], e.get("role")) for e in res["evidence"]] == [("계산", None), ("약관", "중도해지금리 규칙(구간 조회에 쓴 구간표)")]
    assert "가입 후 2개월" in res["evidence_text"] and "심의 제2025-설명서-199호" in res["evidence_text"]


def test_a_failed_search_does_not_block_the_answer_and_the_card_still_shows_the_lookup():
    def broken(q, **kw):
        raise RuntimeError("색인 없음")
    res, m = ask(Q023, broken)
    assert m.n == 0 and res["answer"] == lookup(2)["message"] and res["trace"][1]["status"] == "error"
    assert [e["kind"] for e in res["evidence"]] == ["계산"]


def test_from_six_months_the_answer_asks_for_the_base_rate_instead_of_inventing_a_rate():
    res, m = ask("가입한 지 8개월 됐는데 지금 해지하면 금리가 어떻게 돼요?")
    assert m.n == 0 and "'6개월 이상 ~ 9개월 미만'" in res["answer"] and "기본금리의 60%" in res["answer"] and "기본금리" in res["answer"]
    assert "연 0.15%" not in res["answer"] and "연 0.20%(고정)" not in res["answer"]


def test_a_follow_up_in_the_same_session_goes_on_normally_and_the_lookup_is_not_repeated():
    a, m = tg.agent(lambda msgs, n: tg.ai(tg.call("calc_interest")) if n == 1 else tg.AIMessage(content="(모델)"), search_fn=early_search())
    first = a.ask(Q023, "s", today=TODAY)
    assert first["model_calls"] == 0
    second = a.ask("그러면 이자는 얼마예요?", "s", today=TODAY)
    assert [t["name"] for t in second["trace"]] == ["calc_interest"]                                        # 이자 금액 질문은 계산 도구로 가고, 구간 조회는 다시 부르지 않는다
    assert second["model_calls"] >= 1


def test_a_question_that_is_not_an_elapsed_period_still_reaches_the_model_as_before():
    a, m = tg.agent(lambda msgs, n: tg.AIMessage(content="약관상 그래요." if n == 1 else "검색 결과에 따르면 그래요."), search_fn=tg.rule_search())
    res = a.ask("만기까지 두 달 남았는데 지금 해지하면 이자 거의 못 받나?", "s", today=TODAY)
    assert m.n >= 1 and all(t["name"] != "lookup_termination_rate" for t in res["trace"])


def test_a_handoff_request_and_an_out_of_scope_question_still_win_over_the_lookup():
    a, m = tg.agent(never)
    assert a.ask("가입한 지 두 달 됐는데 상담원 연결해 주세요. 해지하면 금리가 얼마예요?", "s1", today=TODAY)["stop_reason"] == "handoff_saved"
    assert a.ask("대출 금리는 얼마예요?", "s2", today=TODAY)["stop_reason"] == "out_of_scope"                  # 입구 판별의 순서는 그대로다


# ---------- 4. 근거 카드
def test_build_evidence_makes_the_lookup_card_and_the_rule_source_even_without_a_search_result():
    looked = []
    cards = build_evidence([("lookup_termination_rate", lookup(2))], lambda: looked.append(1) or {"status": "ok", "results": [{"rank": 1, "chunk_id": "r", "chunk_type": "중도해지", "source": RULE_SRC, "text": "t"}]})
    assert [c["kind"] for c in cards] == ["계산", "약관"] and looked == [1]
    assert build_evidence([("lookup_termination_rate", lookup(None))]) == []                                   # 조회가 실패하면 근거 카드를 만들지 않는다


# ---------- 5. 채점기와 데이터셋
def test_the_code_only_tool_is_not_counted_as_a_tool_choice_and_q023_passes_the_grader_end_to_end():
    row = next(r for r in map(json.loads, (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines()) if r["id"] == "Q023")
    res, _ = ask(row["question"])
    gr = ev.grade(row, res)
    assert gr["behavior_ok"] and gr["tools_ok"] and gr["facts_ok"] and gr["clean"] and gr["flags"] == [] and gr["gold_chunk_in_evidence"]
    assert gr["tools_model_only"] is False and "search_terms" in gr["forced"]                                   # 코드가 대신 부른 호출이라 '모델만' 점수에는 들지 않는다
    assert ev.CODE_ONLY_TOOLS == tc.CODE_ONLY_TOOLS


def test_the_old_wrong_answer_still_fails_the_fact_grading():
    row = next(r for r in map(json.loads, (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines()) if r["id"] == "Q023")
    wrong = {"answer": "가입한 지 두 달이므로 3개월 이상 ~ 6개월 미만 구간의 중도해지금리를 적용합니다.", "error": None, "stop_reason": None, "model_calls": 2, "ms": 1,
             "trace": [{"name": "search_terms", "status": "ok", "forced": True, "forced_kind": "search", "args": {}}], "evidence": [], "evidence_text": ""}
    gr = ev.grade(row, wrong)
    assert gr["facts_ok"] is False and gr["behavior_ok"] is False
