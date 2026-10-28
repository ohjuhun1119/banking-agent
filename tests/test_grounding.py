"""값 근거 대조(app/grounding.py) 시험.
두 방향을 모두 확인한다: (1) 사용자가 말한 값은 거절하지 않는다 (거짓 거절 없음), (2) 말하지 않은 값은 뺀다."""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import tool_cases  # noqa: E402
from app.grounding import describe_dropped, extract_facts, ground_calc_args  # noqa: E402

TODAY = date(2026, 10, 28)


def dropped_of(args, *texts, known=None, today=TODAY):
    return ground_calc_args(args, list(texts), today, known)[1]


# ---------- 원금 ----------
@pytest.mark.parametrize("text,value", [
    ("1000만원 넣었어요", 10_000_000), ("1,000만 원을 넣었어요", 10_000_000), ("천만 원 넣었어요", 10_000_000),
    ("1천만원이에요", 10_000_000), ("10,000,000원이에요", 10_000_000), ("원금은 10000000이에요", 10_000_000),
    ("5백만원 넣었어요", 5_000_000), ("1억 5천만원이에요", 150_000_000), ("2000만원", 20_000_000), ("100만원", 1_000_000),
])
def test_principal_said_in_many_forms_is_kept(text, value):
    assert dropped_of({"principal": value}, text) == {}


@pytest.mark.parametrize("text,value", [
    ("1000만원 넣었어요", 1_000_000),            # 다른 금액
    ("1000만원 넣었어요", 100_000_000),
    ("금액은 모르겠어요", 10_000_000),             # 말하지 않음
    ("이천만 원 넣었어요", 20_000_000),            # 한글 숫자는 근거로 인정하지 않는다 (되묻는 쪽이 안전)
    ("2026-01-01에 넣었어요", 2_026_000),          # 날짜의 숫자를 금액으로 읽지 않는다
])
def test_principal_not_said_is_dropped(text, value):
    assert dropped_of({"principal": value}, text) == {"principal": value}


def test_principal_with_unreadable_format_is_left_for_the_tool_to_explain():
    clean, dropped = ground_calc_args({"principal": "1000만원"}, ["아무 말"], TODAY)
    assert dropped == {} and clean == {"principal": "1000만원"}                      # 도구의 입력 검사가 안내한다


# ---------- 날짜 ----------
@pytest.mark.parametrize("text", [
    "2026-01-01에 넣었어요", "2026.1.1에 넣었어요", "2026/01/01에 넣었어요", "2026년 1월 1일에 넣었어요", "2026년 01월 01일에 넣었어요",
    "2026-1-1부터", "2026년1월1일 넣었어요",
])
def test_full_date_forms_are_kept(text):
    assert dropped_of({"start_date": "2026-01-01"}, text) == {}


@pytest.mark.parametrize("text,value", [
    ("2026-01-01에 넣었어요", "2026-01-02"),
    ("5개월 전에 넣었어요", "2026-05-28"),          # 상대 날짜를 모델이 계산한 값
    ("가입한 지 150일 됐어요", "2026-05-31"),
    ("어제 넣었어요", "2026-10-27"),
    ("2026-02-30에 넣었어요", "2026-02-28"),        # 없는 날짜는 근거가 아니다
    ("", "2026-01-01"),
])
def test_start_date_not_said_is_dropped(text, value):
    assert dropped_of({"start_date": value}, text) == {"start_date": value}


def test_month_day_without_year_counts_only_for_this_year_or_a_year_the_user_mentioned():
    assert dropped_of({"termination_date": "2026-12-07"}, "12월 7일에 해지할래요") == {}                       # 올해
    assert dropped_of({"termination_date": "2027-12-07"}, "12월 7일에 해지할래요") == {"termination_date": "2027-12-07"}
    assert dropped_of({"termination_date": "2025-12-07"}, "2025-06-01에 넣었고 12월 7일에 해지해요") == {}      # 말한 날짜의 해
    assert dropped_of({"termination_date": "2026-12-08"}, "12월 7일에 해지할래요") == {"termination_date": "2026-12-08"}


@pytest.mark.parametrize("text,kept", [("오늘 해지하면 얼마예요?", True), ("지금 깨면 이자는요?", True), ("당장 해지하면?", True),
                                       ("지금까지 5개월 됐어요", False), ("내일 해지하면?", False), ("해지하면 얼마예요?", False)])
def test_today_words_ground_only_todays_date_as_termination(text, kept):
    assert (dropped_of({"termination_date": "2026-10-28"}, text) == {}) is kept
    assert dropped_of({"termination_date": "2026-10-29"}, text) == {"termination_date": "2026-10-29"}          # 오늘이 아닌 날은 안 됨
    assert dropped_of({"start_date": "2026-10-28"}, text) == {"start_date": "2026-10-28"}                     # 신규일은 '오늘'로 인정하지 않는다


@pytest.mark.parametrize("text,maturity,kept", [
    ("2026년 1월 1일에 천만 원을 1년 만기로 넣었어요", "2027-01-01", True),
    ("2026-01-01에 6개월짜리로 넣었어요", "2026-07-01", True),
    ("2026-01-01에 넣었고 만기 2년이에요", "2028-01-01", True),
    ("2026-01-01에 5년짜리로 넣었어요", "2031-01-01", True),
    ("2026-01-31에 1개월 만기로 넣었어요", "2026-02-28", True),           # 월말 보정은 날짜 계산 도구와 같은 규칙
    ("2026-01-01에 3년짜리로 넣었어요", "2027-01-01", False),             # 기간이 다름
    ("2026-01-01에 1년 동안 넣을 거예요", "2027-01-01", False),            # '만기'나 '짜리' 없이 한 말은 만기 기간으로 보지 않는다
    ("1년 만기로 넣었어요", "2027-01-01", False),                          # 신규일을 말하지 않음
])
def test_maturity_is_grounded_by_a_said_date_or_a_stated_term(text, maturity, kept):
    args = {"start_date": maturity_start(text), "maturity_date": maturity}
    assert ("maturity_date" not in dropped_of(args, text)) is kept


def maturity_start(text):
    return "2026-01-31" if "01-31" in text else "2026-01-01"


def test_maturity_from_a_term_needs_the_start_date_to_be_grounded_too():
    d = dropped_of({"start_date": "2026-01-02", "maturity_date": "2027-01-02"}, "2026-01-01에 1년 만기로 넣었어요")
    assert d == {"start_date": "2026-01-02", "maturity_date": "2027-01-02"}


# ---------- 기본금리 ----------
@pytest.mark.parametrize("text,value", [
    ("기본금리는 3.5%였어요", 3.5), ("기본금리 3.5퍼센트", 3.5), ("3.5프로예요", 3.5), ("기본금리는 3.5였어요", 3.5), ("금리 연 3.50%", 3.5),
    ("3.5", 3.5), ("기본금리 2.0%인데", 2), ("기본금리는 4%예요", 4), ("3.5%였어요", "3.5%"), ("연 3.5%", "3.5"),
])
def test_base_rate_said_is_kept(text, value):
    assert dropped_of({"base_rate": value}, text) == {}


@pytest.mark.parametrize("text,value", [
    ("1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요", 3.5),      # 사용자가 말하지 않은 값: 실제 대화에서 모델이 넣은 값
    ("1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요", 1.5),
    ("2년 만기로 넣었어요", 2),                                         # 기간의 숫자는 금리가 아니다
    ("5개월 됐어요", 5),
    ("1000만원 넣었어요", 1000),                                        # 금액의 숫자
    ("2026-01-01에 넣었어요", 2026),                                    # 날짜의 숫자
    ("12월 7일에 해지해요", 12), ("12월 7일에 해지해요", 7),
    ("10번 물어봤어요", 10),
    ("기본금리는 3.5%였어요", 3.6),                                     # 다른 값
])
def test_base_rate_not_said_is_dropped(text, value):
    assert dropped_of({"base_rate": value}, text) == {"base_rate": value}


# ---------- 앞 대화와 확인된 값 ----------
def test_values_from_several_messages_are_pooled():
    args = {"principal": 10_000_000, "start_date": "2026-01-01", "base_rate": 3.5}
    assert dropped_of(args, "1000만원 넣었어요", "신규일은 2026-01-01이에요", "기본금리는 3.5%였어요") == {}


def test_known_confirmed_values_are_accepted_even_without_the_original_message():
    known = {"principal": 10_000_000, "start_date": "2026-01-01", "base_rate": 3.5}
    assert dropped_of({"principal": 10_000_000, "start_date": "2026-01-01", "base_rate": 3.5}, "그럼 다시 계산해줘", known=known) == {}
    assert dropped_of({"principal": 20_000_000}, "그럼 다시 계산해줘", known=known) == {"principal": 20_000_000}      # 다른 값은 근거가 필요


def test_maturity_can_be_derived_from_a_known_start_date():
    assert dropped_of({"maturity_date": "2027-01-01"}, "1년 만기였어요", known={"start_date": "2026-01-01"}) == {}
    # 모델이 확인된 신규일을 다시 넘겨도 같다 (신규일은 이번 말에 없지만 앞에서 확인된 값)
    assert dropped_of({"start_date": "2026-01-01", "maturity_date": "2027-01-01"}, "1년 만기였어요", known={"start_date": "2026-01-01"}) == {}
    assert dropped_of({"start_date": "2026-01-02", "maturity_date": "2027-01-02"}, "1년 만기였어요", known={"start_date": "2026-01-01"}) == {
        "start_date": "2026-01-02", "maturity_date": "2027-01-02"}


# ---------- 그대로 두는 것 ----------
def test_blank_values_unknown_fields_and_non_text_messages_are_untouched():
    args = {"principal": None, "start_date": "", "maturity_date": "  ", "days": 150}
    clean, dropped = ground_calc_args(args, [None, 3, "아무 말"], TODAY)
    assert dropped == {} and clean == args
    assert ground_calc_args(None, [], TODAY) == ({}, {})


def test_unreadable_dates_and_rates_are_left_for_the_tool():
    args = {"start_date": "20일", "base_rate": "높음"}
    assert ground_calc_args(args, ["아무 말"], TODAY) == (args, {})


def test_clean_args_keep_everything_that_was_not_dropped():
    args = {"principal": 10_000_000, "start_date": "2026-01-01", "base_rate": 3.5}
    clean, dropped = ground_calc_args(args, ["1000만원을 2026-01-01에 넣었어요"], TODAY)
    assert clean == {"principal": 10_000_000, "start_date": "2026-01-01"} and dropped == {"base_rate": 3.5}


def test_describe_dropped():
    assert describe_dropped({"base_rate": 3.5, "start_date": "2026-05-28"}) == "기본금리 3.5, 신규일 2026-05-28"


def test_extract_facts_does_not_read_durations_or_dates_as_amounts_or_rates():
    f = extract_facts("2026-01-01에 2년 만기로 1000만원 넣었고 12월 7일에 3개월 지났어요")
    assert f.rates == set() and 10_000_000 in f.amounts and f.terms == [(2, "year")]
    assert date(2026, 1, 1) in f.dates and (12, 7) in f.month_days


# ---------- 확인용 질문 25개로 확인 ----------
CALC_CASES = [c for c in tool_cases.CASES if c["expect"]["tool"] == "calc_interest"]
TODAY_CASES = date.fromisoformat(tool_cases.TODAY)


@pytest.mark.parametrize("case", CALC_CASES, ids=[c["id"] for c in CALC_CASES])
def test_every_expected_argument_of_the_check_questions_is_grounded(case):
    """질문에 적힌 값(또는 질문에서 정해지는 값)을 거짓으로 거절하지 않는다."""
    exp = case["expect"]
    args = exp["args"] if exp["strict"] else exp["allowed"]
    assert ground_calc_args(args, [case["question"]], TODAY_CASES)[1] == {}


# 5일차 측정에서 실제로 모델이 낸 값 (eval/tool_runs의 실패 기록). 모두 근거가 없어서 빠져야 한다.
REAL_MODEL_OUTPUTS = [
    ("C1", {"base_rate": 3.5, "maturity_date": "2027-01-01", "principal": 10000000, "start_date": "2026-01-01", "termination_date": "2026-01-21"}, {"base_rate"}),
    ("C2", {"principal": 10000000, "start_date": "2026-01-01", "termination_date": "2026-03-02", "base_rate": 1.5, "maturity_date": "2027-01-01"}, {"base_rate"}),
    ("C3", {"maturity_date": "2027-01-01", "principal": 10000000, "start_date": "2026-01-01", "termination_date": "2026-05-31", "base_rate": 3.5}, {"base_rate"}),
    ("C5", {"principal": 10000000, "start_date": "2024-01-01", "maturity_date": "2028-01-01", "termination_date": "2026-10-28"}, {"start_date"}),
    ("M3", {"principal": 5000000, "start_date": "2026-05-10", "termination_date": "2026-10-28"}, {"start_date"}),
    ("M4", {"base_rate": 3.5, "principal": 20000000, "start_date": "2026-05-28", "termination_date": "2026-10-28"}, {"base_rate", "start_date"}),
    ("M4", {"maturity_date": "2027-10-28", "principal": 20000000, "start_date": "2026-10-28", "termination_date": "2026-11-28", "base_rate": 1.5},
     {"maturity_date", "start_date", "termination_date", "base_rate"}),
    ("M5", {"base_rate": 1.5, "maturity_date": "2026-02-01", "principal": 1000000, "start_date": "2026-02-01", "termination_date": "2026-09-30"}, {"base_rate", "principal"}),
    ("E1", {"start_date": "2026-01-01", "termination_date": "2026-05-31", "base_rate": 1.5, "maturity_date": "2027-01-01", "principal": 100000}, {"base_rate"}),
]


@pytest.mark.parametrize("cid,args,expected", REAL_MODEL_OUTPUTS, ids=[f"{c}-{i}" for i, (c, _, _) in enumerate(REAL_MODEL_OUTPUTS)])
def test_values_the_model_really_invented_are_dropped(cid, args, expected):
    question = next(c["question"] for c in tool_cases.CASES if c["id"] == cid)
    assert set(ground_calc_args(args, [question], TODAY_CASES)[1]) == expected


def test_what_the_guard_cannot_see_is_left_to_the_validator():
    """값이 말해졌는지만 본다. 신규일을 만기일 칸에 넣은 M5의 모델 출력은 근거 대조를 통과하지만 입력 검사가 막는다."""
    from app.tools.interest import calc_interest
    q = next(c["question"] for c in tool_cases.CASES if c["id"] == "M5")
    args = {"principal": 10_000_000, "start_date": "2026-02-01", "maturity_date": "2026-02-01", "termination_date": "2026-09-30"}
    clean, dropped = ground_calc_args(args, [q + " 1000만원"], TODAY_CASES)
    assert dropped == {} and calc_interest(**clean)["status"] == "invalid_input"
