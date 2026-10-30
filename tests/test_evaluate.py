"""평가 채점기(eval/evaluate.py) 시험 (10일차): 점수의 정의가 코드와 같은지, 실제 모델 기록에서 본 실패를 잡는지."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import evaluate as ev  # noqa: E402
from eval import heldout_run  # noqa: E402

TODAY = "2026-10-28"


def q(behavior, tools=(), allowed=(), **gold):
    return {"id": "Q1", "category": "rule", "question": "정기예금 중도해지하면 이율이 어떻게 돼요?", "today": TODAY,
            "gold": {"behavior": behavior, "tools": list(tools), "tools_allowed": list(allowed), **gold}}


def t(name, status="ok", **kw):
    return {"name": name, "args": kw.pop("args", {}), "status": status, "ms": 5, "forced": False, "forced_kind": None, "retried": False, "duplicate": False,
            "dropped": {}, "filled": {}, "ignored": [], **kw}


def res(answer="답변이에요.", trace=(), evidence=(), **kw):
    return {"answer": answer, "error": None, "stop_reason": None, "tool_calls": len(trace), "model_calls": 2, "ms": 1000, "trace": list(trace), "evidence": list(evidence),
            "evidence_text": kw.pop("evidence_text", ""), **kw}


CARD = {"kind": "약관", "chunk_id": "hana-deposit-sheet__early_termination"}
CALC_ARGS = {"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-12-07", "base_rate": 3.5}


# ---------- 규칙 질문 ----------
def test_a_searched_rule_answer_with_evidence_is_clean_and_the_gold_chunk_is_found():
    g = ev.grade(q("search", ["search_terms"], chunks=["hana-deposit-sheet__early_termination"]), res(trace=[t("search_terms")], evidence=[CARD]))
    assert g["behavior_ok"] and g["tools_ok"] and g["tools_model_only"] and g["evidence_ok"] and g["gold_chunk_in_evidence"] and g["clean"] and not g["flags"]


def test_evidence_that_lacks_the_gold_chunk_is_reported_separately_from_having_evidence():
    g = ev.grade(q("search", ["search_terms"], chunks=["hana-deposit-sheet__renewal"]), res(trace=[t("search_terms")], evidence=[CARD]))
    assert g["evidence_ok"] and g["gold_chunk_in_evidence"] is False and g["behavior_ok"]


def test_a_search_answer_without_an_evidence_card_is_flagged():
    g = ev.grade(q("search", ["search_terms"]), res(trace=[t("search_terms")]))
    assert g["evidence_ok"] is False and "no_evidence" in g["flags"]


def test_a_search_that_found_nothing_does_not_count_as_a_searched_answer():
    g = ev.grade(q("search", ["search_terms"]), res(trace=[t("search_terms", "not_found")]))
    assert not g["behavior_ok"] and "evidence_ok" not in g


def test_a_tool_the_code_called_for_the_model_counts_as_tools_ok_but_not_as_chosen_by_the_model():
    g = ev.grade(q("search", ["search_terms"]), res(trace=[t("search_terms", forced=True, forced_kind="search")], evidence=[CARD]))
    assert g["tools_ok"] and g["tools_model_only"] is False and g["forced"] == ["search_terms"] and g["behavior_ok"]


def test_calling_the_calculator_on_a_rule_question_is_a_wrong_tool_even_when_nothing_unsafe_was_said():
    """검증용 H14 · H7: 방법 질문에 모델이 계산 도구를 부름."""
    g = ev.grade(q("search", ["search_terms"]), res("원금과 신규일을 알려주세요.", [t("calc_interest", "missing_info", args={"principal": 5})]))
    assert not g["tools_ok"] and not g["behavior_ok"] and not g["clean"]


# ---------- 계산 ----------
CALC_Q = q("calc_ok", ["calc_interest"], calc={"args": CALC_ARGS, "status": "ok", "interest_krw": 273304, "rate_pct": "2.934"})


def calc_res(answer, **kw):
    return res(answer, [t("calc_interest", "ok", args=dict(CALC_ARGS), **kw)], evidence=[{"kind": "계산"}])


def test_a_calculation_with_the_right_amount_passes_and_a_wrong_or_missing_amount_does_not():
    assert ev.grade(CALC_Q, calc_res("세전 이자는 273,304원이에요."))["calc_value_ok"] is True
    assert ev.grade(CALC_Q, calc_res("세전 이자는 273304원이에요."))["calc_value_ok"] is True
    bad = ev.grade(CALC_Q, calc_res("세전 이자는 280,000원이에요."))
    assert bad["calc_value_ok"] is False and not bad["behavior_ok"]
    assert ev.grade(CALC_Q, res("계산했어요.", [t("calc_interest", "need_base_rate", args=dict(CALC_ARGS))]))["calc_value_ok"] is False


def test_args_are_judged_on_what_the_tool_really_used_after_the_code_dropped_and_filled_values():
    ok = ev.grade(CALC_Q, res("세전 이자는 273,304원이에요.", [t("calc_interest", "ok", args={**CALC_ARGS, "principal": 99, "maturity_date": None},
                                                         dropped={"principal": 99}, filled={"principal": 10_000_000, "maturity_date": "2027-01-01"})], evidence=[{"kind": "계산"}]))
    assert ok["args_ok"] is True                                         # 모델이 틀리게 넘겼어도 코드가 바로잡은 값으로 판단
    wrong = ev.grade(CALC_Q, res("세전 이자는 273,304원이에요.", [t("calc_interest", "ok", args={**CALC_ARGS, "principal": 20_000_000})], evidence=[{"kind": "계산"}]))
    assert wrong["args_ok"] is False


def test_a_stated_value_the_tool_never_received_is_flagged_as_omitted_and_asking_for_it_again_as_reasked():
    g = ev.grade(CALC_Q, res("만기일을 알려주세요.", [t("calc_interest", "missing_info", args={"principal": 10_000_000, "termination_date": "2026-12-07"})]))
    assert {"omitted_stated:start_date", "omitted_stated:maturity_date", "reasked_stated:maturity_date"} <= set(g["flags"])


def test_unsupported_gold_fields_are_not_held_against_the_system():
    gq = q("calc_ok", ["calc_interest"], calc={"args": {**CALC_ARGS, "termination_date": "2026-07-31"}, "status": "ok", "interest_krw": 1}, unsupported=["termination_date"])
    g = ev.grade(gq, res("해지일을 알려주세요.", [t("calc_interest", "missing_info", args={k: v for k, v in CALC_ARGS.items() if k != "termination_date"})]))
    assert not any(f.endswith("termination_date") for f in g["flags"])


def test_need_base_rate_must_actually_ask_for_the_base_rate():
    gq = q("ask_base_rate", ["calc_interest"], calc={"args": CALC_ARGS, "status": "need_base_rate"}, missing=["기본금리"])
    assert ev.grade(gq, res("신규일 당시 기본금리(연 %)는 얼마였나요?", [t("calc_interest", "need_base_rate", args=dict(CALC_ARGS))], evidence=[CARD]))["behavior_ok"]
    assert not ev.grade(gq, res("계산했습니다.", [t("calc_interest", "need_base_rate", args=dict(CALC_ARGS))], evidence=[CARD]))["behavior_ok"]


def test_a_calculation_that_finishes_when_values_were_missing_is_a_fabricated_ok():
    gq = q("ask_missing", [], ["calc_interest"], calc={"args": {"principal": 10_000_000}, "status": "missing_info"}, missing=["신규일", "만기일"])
    g = ev.grade(gq, res("세전 이자는 1원이에요.", [t("calc_interest", "ok", args={"principal": 10_000_000, "start_date": "2026-01-01"})]))
    assert "fabricated_ok" in g["flags"] and g["unsupported"] and not g["behavior_ok"] and not g["clean"]


def test_asking_for_missing_values_with_or_without_calling_the_calculator():
    gq = q("ask_missing", [], ["calc_interest"], calc={"args": {"principal": 10_000_000}, "status": "missing_info"}, missing=["신규일", "만기일"])
    assert ev.grade(gq, res("신규일과 만기일을 알려주세요."))["behavior_ok"]                                          # 도구 없이 되물음
    called = ev.grade(gq, res("신규일을 알려주세요.", [t("calc_interest", "missing_info", args={"principal": 10_000_000})]))
    assert called["behavior_ok"] and called["tools_ok"]
    assert not ev.grade(gq, res("이자가 궁금하시군요."))["behavior_ok"]                                               # 되묻지 않음
    assert not ev.grade(gq, res("원금과 해지일을 알려주세요."))["behavior_ok"]                                         # 엉뚱한 칸을 물음


# ---------- 그 밖의 행동 ----------
def test_not_early_invalid_handoff_no_tool_and_scope():
    ne = q("not_early", ["calc_interest"], calc={"args": CALC_ARGS, "status": "not_early_termination"})
    assert ev.grade(ne, res("해지일이 만기일 이후라 중도해지가 아닙니다.", [t("calc_interest", "not_early_termination", args=dict(CALC_ARGS))]))["behavior_ok"]
    inv = q("invalid_input", ["calc_interest"], calc={"args": CALC_ARGS, "status": "invalid_input"})
    assert ev.grade(inv, res("최소 가입금액은 100만원입니다.", [t("calc_interest", "invalid_input", args=dict(CALC_ARGS))]))["behavior_ok"]
    saved = q("handoff_saved", ["request_handoff"])
    assert ev.grade(saved, res("상담 요청을 저장했어요. 접수번호는 20261028-0001예요.", [t("request_handoff", "saved", forced=True, forced_kind="handoff")], stop_reason="handoff_saved"))["clean"]
    assert not ev.grade(saved, res("상담 요청을 저장했어요. 접수번호는 20261028-0001예요."))["behavior_ok"]               # 실제로 저장하지 않았다
    conf = q("handoff_confirm", ["request_handoff"])
    assert ev.grade(conf, res("상담 요청을 저장할까요? 저장을 원하시면 '네'라고 답해 주세요.", [t("request_handoff", "needs_confirmation")]))["behavior_ok"]
    assert not ev.grade(conf, res("상담 요청을 저장할까요?", [t("request_handoff", "needs_confirmation")], stop_reason="handoff_saved"))["behavior_ok"]
    nt = q("no_tool")
    assert ev.grade(nt, res("안녕하세요! 무엇을 도와드릴까요?"))["behavior_ok"] and not ev.grade(nt, res("안녕하세요", [t("search_terms")]))["behavior_ok"]
    sc = q("scope", [], ["search_terms"], must_not=["접수번호"])
    assert ev.grade(sc, res("대출은 안내하지 않아요.", [t("search_terms", "not_found")]))["behavior_ok"]
    assert not ev.grade(sc, res("계산했어요.", [t("calc_interest", "missing_info")]))["behavior_ok"]
    assert not ev.grade(sc, res("접수번호는 1이에요."))["behavior_ok"]


def test_must_say_and_must_not_decide_the_behavior_and_may_be_satisfied_by_the_evidence_card():
    gq = q("search", ["search_terms"], must_say_any=["휴일", "은행에 확인"], must_not=["확정"])
    base = dict(trace=[t("search_terms")], evidence=[CARD])
    assert ev.grade(gq, res("가능해요.", **base, evidence_text="주의: 만기일이 휴일입니다"))["behavior_ok"]          # 근거 카드에 있어도 된다
    assert not ev.grade(gq, res("가능해요.", **base))["behavior_ok"]
    assert not ev.grade(gq, res("휴일이라 확정됩니다.", **base))["behavior_ok"]


def test_a_crashed_or_failed_model_call_is_a_failure_and_a_flag():
    g = ev.grade(q("search", ["search_terms"]), {**res(None), "error": "모델 호출 실패", "stop_reason": "model_error"})
    assert not g["behavior_ok"] and "model_error" in g["flags"] and g["unsupported"] is False and not g["clean"]


# ---------- 근거 없는 답변 표시 ----------
@pytest.mark.parametrize("answer,trace,flag", [
    ("약관에서 확인된 내용은 解释です.", [], "foreign_script"),
    ("고객센터 1544-1111로 전화하세요.", [], "phone_in_answer"),
    ("(calc_interest {principal: 1})", [], "tool_syntax"),
    ("약관에서 확인되지 않습니다.", [], "unsupported_not_found"),
    ("세금을 뺀 실제 들어오는 금액을 알려드릴 수 있습니다.", [], "promises_after_tax"),
    ("상담 요청을 접수했어요.", [], "handoff_claim"),
    ("날짜를 YYYY-MM-DD 형식으로 입력해 주세요.", [], "asks_format"),
    ("사용자가 말하지 않은 해지일은 계산에 사용하지 않았습니다.", [], "note_leak"),
    ("신규일은 2023년 1월 1일로 잡았습니다.", [], "ungrounded_in_answer"),
])
def test_unsupported_answer_flags(answer, trace, flag):
    g = ev.grade(q("scope", [], ["search_terms"]), res(answer, trace))
    assert flag in g["flags"]
    assert g["unsupported"] is (flag != "asks_format") and g["clean"] is False


def test_a_not_found_claim_is_fine_after_a_search_that_found_nothing_and_an_ungrounded_date_is_fine_after_a_search():
    ok = ev.grade(q("scope", [], ["search_terms"]), res("약관에서 확인되지 않습니다.", [t("search_terms", "not_found")]))
    assert "unsupported_not_found" not in ok["flags"]
    searched = ev.grade(q("search", ["search_terms"]), res("2025년 9월 1일 기준 약관에 따르면 가능해요.", [t("search_terms")], [CARD]))
    assert "ungrounded_in_answer" not in searched["flags"]


def test_the_confirmation_receipt_number_is_not_a_claim_when_it_really_was_saved():
    g = ev.grade(q("handoff_saved", ["request_handoff"]), res("접수번호는 20261028-0001예요.", [t("request_handoff", "saved")], stop_reason="handoff_saved"))
    assert "handoff_claim" not in g["flags"] and g["clean"]
    other_day = ev.grade(q("handoff_saved", ["request_handoff"]), res("상담 요청을 저장했어요. 접수번호는 20261029-0001예요.", [t("request_handoff", "saved")], stop_reason="handoff_saved"))
    assert other_day["flags"] == [] and other_day["clean"]                   # 접수번호 속 날짜(오늘이 아닌 날)를 근거 없는 날짜로 보지 않는다
    failed = ev.grade(q("scope", [], ["request_handoff"]), res("상담 요청을 저장하지 못했어요. 20261029에 다시 시도하세요.", stop_reason="handoff_failed"))
    assert "ungrounded_in_answer" not in failed["flags"]


def test_unknown_tool_arguments_are_flagged_but_are_not_counted_as_unsupported_answers():
    g = ev.grade(q("ask_missing", [], ["calc_interest"]), res("값을 알려주세요.", [t("calc_interest", "invalid_input", args={"filled_args": {}})]))
    assert "unknown_arg:filled_args" in g["flags"] and g["unsupported"] is False


# ---------- 요약 ----------
def rows_for_summary():
    def row(i, cat, behavior_ok, unsupported, ms, forced=(), extra=None):
        g = {"behavior_ok": behavior_ok, "tools_ok": True, "tools_model_only": not forced, "flags": ["note_leak"] if unsupported else [], "unsupported": unsupported,
             "clean": behavior_ok and not unsupported, "forced": list(forced), "ms": ms, "model_calls": 2, **(extra or {})}
        return {"id": f"Q{i}", "category": cat, "question": "q", "grade": g}
    return [row(1, "rule", True, False, 1000, extra={"evidence_ok": True, "gold_chunk_in_evidence": True}), row(2, "rule", True, True, 3000, ["search_terms"], {"evidence_ok": False}),
            row(3, "calc", False, False, 2000, extra={"calc_value_ok": False, "args_ok": True}), row(4, "calc", True, False, 4000, extra={"calc_value_ok": True, "args_ok": True})]


def test_summary_counts_overall_and_per_category():
    s = ev.summarize(rows_for_summary())
    o, rule, calc = s["overall"], s["by_category"]["rule"], s["by_category"]["calc"]
    assert o["n"] == 4 and o["behavior_ok"] == {"hit": 3, "n": 4} and o["clean"] == {"hit": 2, "n": 4} and o["unsupported"] == {"hit": 1, "n": 4}
    assert o["forced"] == {"hit": 1, "n": 4} and o["tools_model_only"] == {"hit": 3, "n": 4} and o["flags"] == {"note_leak": 1}
    assert rule["evidence_ok"] == {"hit": 1, "n": 2} and rule["gold_chunk_in_evidence"] == {"hit": 1, "n": 1} and "calc_value_ok" not in rule
    assert calc["calc_value_ok"] == {"hit": 1, "n": 2} and calc["args_ok"] == {"hit": 2, "n": 2}
    assert o["ms_p50"] == 2000 and o["ms_p90"] == 4000 and o["model_calls_avg"] == 2.0


def test_percentile_uses_the_nearest_rank_and_ignores_missing_values():
    assert ev.percentile([1000, 2000, 3000, 4000], 50) == 2000 and ev.percentile([1000, 2000, 3000, 4000], 90) == 4000
    assert ev.percentile([5], 99) == 5 and ev.percentile([None, 7], 50) == 7 and ev.percentile([], 50) is None


# ---------- 검색 품질 ----------
def test_retrieval_metrics_count_recall_rank_and_traps_ahead_of_the_gold():
    qs = [q("search", ["search_terms"], chunks=["A"], traps=["T"]) | {"id": "Q1", "question": "하나"}, q("search", ["search_terms"], chunks=["B"]) | {"id": "Q2", "question": "둘"},
          q("search", ["search_terms"], chunks=["C"]) | {"id": "Q3", "question": "셋"}, q("scope") | {"id": "Q4", "question": "정답 없음"}]
    ranked = {"하나": ["T", "A", "x"], "둘": ["B", "y"], "셋": ["x", "y", "z"]}
    m = ev.retrieval_metrics(qs, lambda query, k: {"status": "ok", "results": [{"chunk_id": c} for c in ranked[query][:k]]}, k=3)
    assert m["n"] == 3 and m["missed"] == ["Q3"] and m["trap_before_gold"] == 1
    assert m["recall_at_k"] == pytest.approx(2 / 3) and m["recall_at_1"] == pytest.approx(1 / 3) and m["mrr"] == pytest.approx((1 / 2 + 1 + 0) / 3)
    assert ev.retrieval_metrics([], lambda *a, **k: {}, 3)["recall_at_k"] is None


# ---------- 기존 채점기와 어긋나지 않는다 ----------
SHARED = {"foreign_script", "phone_in_answer", "tool_syntax", "unsupported_not_found", "promises_after_tax", "asks_format", "note_leak", "ungrounded_in_answer"}


@pytest.mark.parametrize("fixture", ["heldout_before_fix.json", "heldout_after_fix.json"])
def test_the_safety_flags_agree_with_the_held_out_grader_on_the_recorded_real_model_answers(fixture):
    """같은 검사를 같은 함수(answer_check)로 하는 두 채점기가 실제 모델 기록 20문항에서 같은 표시를 낸다."""
    held = {h["id"]: h for h in json.loads((ROOT / "eval" / "heldout" / "questions.json").read_text(encoding="utf-8"))["questions"]}
    rows = json.loads((ROOT / "tests" / "fixtures" / fixture).read_text(encoding="utf-8"))["rows"]
    assert len(rows) >= 12
    for row in rows:
        hq = held[row["id"]]
        r = {"answer": row["answer"], "error": row.get("error"), "trace": row["trace"], "today": TODAY}
        theirs = {f for f in heldout_run.grade(hq, r)["flags"] if f in SHARED or f.startswith("unknown_arg:")}
        mine = {f for f in ev.safety_flags({"question": hq["question"], "today": TODAY, "gold": {"behavior": "scope"}}, r) if f in SHARED or f.startswith("unknown_arg:")}
        assert mine == theirs, (row["id"], mine, theirs)


# ---------- 변형 시험이 짚은 빈틈 (도구 · 되묻기 · 안내 · 뺀 값) ----------
def test_a_required_tool_that_was_never_called_is_not_tools_ok_even_if_nothing_forbidden_was_called():
    g = ev.grade(q("search", ["search_terms"]), res("약관상 가능해요."))
    assert g["tools_ok"] is False and g["tools_model_only"] is False and not g["behavior_ok"]


def test_a_forbidden_tool_called_together_with_the_required_one_is_not_tools_ok():
    both = res("약관상 가능해요.", [t("search_terms"), t("calc_interest", "missing_info")], [CARD])
    g = ev.grade(q("search", ["search_terms"]), both)
    assert g["tools_ok"] is False and g["tools_model_only"] is False
    assert ev.grade(q("search", ["search_terms"], allowed=["calc_interest"]), both)["tools_ok"] is True                # 허용하면 통과


@pytest.mark.parametrize("kind", ["search", "calc"])
def test_a_code_forced_call_of_either_kind_is_not_counted_as_the_models_own_choice(kind):
    name = "search_terms" if kind == "search" else "calc_interest"
    gq = q("search", [name]) if kind == "search" else q("ask_missing", [], [name])
    g = ev.grade(gq, res("신규일을 알려주세요.", [t(name, "ok" if kind == "search" else "missing_info", forced=True, forced_kind=kind)], [CARD]))
    assert g["tools_ok"] is True and g["tools_model_only"] is False and g["forced"] == [name]


def test_asking_for_values_after_the_calculation_already_finished_is_not_the_expected_behavior():
    gq = q("ask_missing", [], ["calc_interest"], calc={"args": {"principal": 10_000_000}, "status": "missing_info"}, missing=["신규일"])
    g = ev.grade(gq, res("신규일을 알려주세요.", [t("calc_interest", "ok", args={"principal": 10_000_000})]))
    assert g["behavior_ok"] is False and "fabricated_ok" in g["flags"]


def test_a_not_early_answer_must_actually_say_it_is_not_an_early_termination():
    ne = q("not_early", ["calc_interest"], calc={"args": CALC_ARGS, "status": "not_early_termination"})
    assert not ev.grade(ne, res("알겠습니다.", [t("calc_interest", "not_early_termination", args=dict(CALC_ARGS))]))["behavior_ok"]


def test_a_value_the_code_dropped_and_did_not_refill_counts_as_not_used():
    """모델이 틀린 원금을 넘겼고 코드가 뺐지만 질문에서 못 채웠다: 계산 도구는 원금 없이 불렸다."""
    g = ev.grade(CALC_Q, res("원금을 알려주세요.", [t("calc_interest", "missing_info", args={**CALC_ARGS, "principal": 20_000_000}, dropped={"principal": 20_000_000})]))
    assert g["args_ok"] is False and "omitted_stated:principal" in g["flags"]


def test_asking_for_the_base_rate_also_needs_an_evidence_card():
    gq = q("ask_base_rate", ["calc_interest"], calc={"args": CALC_ARGS, "status": "need_base_rate"}, missing=["기본금리"])
    g = ev.grade(gq, res("신규일 당시 기본금리(연 %)는 얼마였나요?", [t("calc_interest", "need_base_rate", args=dict(CALC_ARGS))]))
    assert g["evidence_ok"] is False and "no_evidence" in g["flags"] and not g["clean"]


def test_a_trap_chunk_that_comes_after_the_gold_chunk_is_not_counted_as_ahead_of_it():
    qs = [q("search", ["search_terms"], chunks=["A"], traps=["T"]) | {"id": "Q1", "question": "하나"}]
    after = ev.retrieval_metrics(qs, lambda query, k: {"results": [{"chunk_id": c} for c in ["A", "T"]]}, k=3)
    before = ev.retrieval_metrics(qs, lambda query, k: {"results": [{"chunk_id": c} for c in ["T", "A"]]}, k=3)
    assert after["trap_before_gold"] == 0 and before["trap_before_gold"] == 1


# ---------- 첫 실제 모델 실행(2026-10-30)을 읽고 찾은 채점기의 결함 ----------
from app import graph as _g  # noqa: E402
from app.answer_check import READ_VALUES_RE  # noqa: E402


def test_the_values_the_code_appends_to_a_question_are_not_the_models_claim():
    """검증용 X002: 코드가 '1년 만기'에서 계산한 만기일(2027-03-15)을 덧붙였는데 근거 없는 날짜로 표시됐다."""
    qx = {"question": "5천만 원을 3월 15일에 가입해서 1년 만기로 들었어. 다음 주에 해지하면 세후로 얼마 받아?", "today": TODAY, "gold": {"behavior": "ask_missing", "tools": [], "tools_allowed": ["calc_interest"]}}
    code_text = "계산에 필요한 값이 없습니다: 해지일. 이 값을 알려주세요. (질문에서 읽은 값: 신규일 2026-03-15, 원금 50,000,000원, 만기일 2027-03-15)"
    assert "ungrounded_in_answer" not in ev.safety_flags(qx, res(code_text, [t("calc_interest", "missing_info", forced=True, forced_kind="calc")]))
    invented = "계산에 필요한 값이 없습니다: 해지일. 만기일은 2027-03-15로 잡았습니다."          # 모델이 직접 쓴 날짜는 여전히 잡는다
    assert "ungrounded_in_answer" in ev.safety_flags(qx, res(invented, [t("calc_interest", "missing_info")]))


def test_the_grader_removes_exactly_what_the_graph_appends():
    """채점기가 떼는 글의 모양이 그래프가 실제로 덧붙이는 글과 같다 (한쪽만 바뀌면 여기서 잡힌다)."""
    state = {"messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage("질문"),
                          __import__("langchain_core.messages", fromlist=["ToolMessage"]).ToolMessage(
                              content=json.dumps({"status": "missing_info", "message": "값을 알려주세요.", "filled_args": {"원금": 50_000_000, "만기일": "2027-03-15"}}, ensure_ascii=False),
                              name="calc_interest", tool_call_id="c")],
             "trace": [{"name": "calc_interest", "forced": True, "forced_kind": "calc", "status": "missing_info"}]}
    text = _g.verbatim_question(state)
    assert "질문에서 읽은 값: " in text and READ_VALUES_RE.sub("", text) == "값을 알려주세요."


def test_the_minimum_deposit_the_tool_states_is_a_product_fact_not_an_invented_amount():
    """개발용 Q060(E1): '최소 가입금액은 100만원입니다'가 근거 없는 금액으로 표시됐다."""
    qx = {"question": "10만원을 2026-01-01부터 2027-01-01까지 넣었다가 2026-05-31에 해지하면 이자 얼마예요?", "today": TODAY, "gold": {"behavior": "invalid_input", "tools": ["calc_interest"]}}
    answer = "최소 가입금액은 100만원입니다. 입력하신 100,000원은 『하나의 정기예금』에 가입할 수 없는 금액이라 이자를 계산하지 않습니다."
    assert "ungrounded_in_answer" not in ev.safety_flags(qx, res(answer, [t("calc_interest", "invalid_input")]))


@pytest.mark.parametrize("fixed", sorted(ev.REPLACED_TEXTS))
def test_a_fixed_replacement_text_is_not_an_answer_even_when_the_search_worked(fixed):
    """검증용 X014: 점검에 걸려 코드가 대신 보낸 '답변을 드리지 못했어요'가 '검색했으니 행동 OK'로 채점됐다."""
    g = ev.grade(q("search", ["search_terms"]), res(fixed, [t("search_terms")], evidence=[CARD]))
    assert "replaced_answer" in g["flags"] and g["behavior_ok"] is False and g["clean"] is False and g["unsupported"] is False


def test_a_scope_question_may_call_the_calculator_only_when_the_gold_allows_it():
    """개발용 Q045 '세후 이자는 얼마예요?': 허용 도구에 계산이 있는데 행동 규칙이 계산을 금지해 정답이 스스로 모순이었다."""
    allowed = q("scope", [], ["search_terms", "calc_interest"], must_say_any=["세전", "원천징수", "세금"])
    asks = res("계산에 필요한 값이 없습니다: 원금, 신규일, 만기일, 해지일. 이 값을 알려주세요.", [t("calc_interest", "missing_info")])
    assert ev.grade(allowed, asks)["behavior_ok"] is False                                   # 세전 · 세금 안내가 없어서 실패: 도구 때문이 아니다
    explains = res("세후 이자는 계산하지 않고 세전 이자만 안내해요. 원금과 날짜를 알려주세요.", [t("calc_interest", "missing_info")])
    assert ev.grade(allowed, explains)["behavior_ok"] is True
    forbidden = q("scope", [], ["search_terms"])
    assert ev.grade(forbidden, res("값을 알려주세요.", [t("calc_interest", "missing_info")]))["behavior_ok"] is False


def test_the_set_of_replacement_texts_is_exactly_the_four_the_code_can_send_instead_of_an_answer():
    assert ev.REPLACED_TEXTS == {_g.UNSAFE_TEXT, _g.EMPTY_TEXT, _g.TOOL_ERROR_TEXT, _g.LIMIT_TEXT}
    assert _g.HANDOFF_CONFIRM_TEXT not in ev.REPLACED_TEXTS and _g.NO_CONNECT_TEXT not in ev.REPLACED_TEXTS           # 저장 확인 질문은 정상 응답이다
