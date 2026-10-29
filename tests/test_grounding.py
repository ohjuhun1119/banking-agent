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
    ("일천만원을 예금했어요", 10_000_000), ("이천만원 가입했어요", 20_000_000), ("천 오백만 원 넣었어요", 15_000_000), ("1.5억 넣었어요", 150_000_000),
    ("2.5천만원 넣었어요", 25_000_000), ("3억 넣었어요", 300_000_000),
])
def test_principal_said_in_many_forms_is_kept(text, value):
    assert dropped_of({"principal": value}, text) == {}


@pytest.mark.parametrize("text,value", [
    ("1000만원 넣었어요", 1_000_000),            # 다른 금액
    ("1000만원 넣었어요", 100_000_000),
    ("금액은 모르겠어요", 10_000_000),             # 말하지 않음
    ("2026-01-01에 넣었어요", 2_026_000),          # 날짜의 숫자를 금액으로 읽지 않는다
])
def test_principal_not_said_is_dropped(text, value):
    assert dropped_of({"principal": value}, text) == {"principal": value}


def test_principal_with_unreadable_format_is_left_for_the_tool_only_if_the_user_wrote_it():
    clean, dropped = ground_calc_args({"principal": "1000만원"}, ["1000만원 넣었어요"], TODAY)
    assert dropped == {} and clean == {"principal": "1000만원"}                      # 사용자가 쓴 글자: 도구의 입력 검사가 안내한다
    clean, dropped = ground_calc_args({"principal": "1000만원"}, ["아무 말"], TODAY)
    assert dropped == {"principal": "1000만원"} and clean == {}                      # 말하지 않은 글자: 지어낸 값이므로 뺀다


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
                                       ("지금까지 5개월 됐어요", False), ("해지하면 얼마예요?", False)])
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


def test_unreadable_dates_and_rates_are_left_for_the_tool_only_if_the_user_wrote_them():
    args = {"start_date": "20일", "base_rate": "높음"}
    assert ground_calc_args(args, ["20일에 가입했고 금리는 높음이래요"], TODAY) == (args, {})          # 사용자가 쓴 글자: 도구가 안내
    clean, dropped = ground_calc_args(args, ["아무 말"], TODAY)
    assert clean == {} and dropped == args                                                           # 말하지 않은 글자: 뺀다


def test_clean_args_keep_everything_that_was_not_dropped():
    args = {"principal": 10_000_000, "start_date": "2026-01-01", "base_rate": 3.5}
    clean, dropped = ground_calc_args(args, ["1000만원을 2026-01-01에 넣었어요"], TODAY)
    assert clean == {"principal": 10_000_000, "start_date": "2026-01-01"} and dropped == {"base_rate": 3.5}


def test_describe_dropped_names_the_fields_but_never_the_values():
    assert describe_dropped({"base_rate": 3.5, "start_date": "2026-05-28"}) == "기본금리, 신규일"        # 값을 말하면 모델이 따라 말한다
    assert "3.5" not in describe_dropped({"base_rate": 3.5}) and describe_dropped({}) == ""


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
    ("M5", {"base_rate": 1.5, "maturity_date": "2026-02-01", "principal": 1000000, "start_date": "2026-02-01", "termination_date": "2026-09-30"},
     {"base_rate", "principal", "maturity_date"}),                       # 가입한 날(2026-02-01)을 만기일 칸에 넣은 것도 문맥으로 걸러진다
    ("E1", {"start_date": "2026-01-01", "termination_date": "2026-05-31", "base_rate": 1.5, "maturity_date": "2027-01-01", "principal": 100000}, {"base_rate"}),
]


@pytest.mark.parametrize("cid,args,expected", REAL_MODEL_OUTPUTS, ids=[f"{c}-{i}" for i, (c, _, _) in enumerate(REAL_MODEL_OUTPUTS)])
def test_values_the_model_really_invented_are_dropped(cid, args, expected):
    question = next(c["question"] for c in tool_cases.CASES if c["id"] == cid)
    assert set(ground_calc_args(args, [question], TODAY_CASES)[1]) == expected


def test_what_the_guard_cannot_see_is_left_to_the_validator():
    """날짜의 문맥(가입 · 만기 · 해지)이 없으면 어느 칸의 값인지 알 수 없다. 신규일과 만기일을 바꿔 넣은 것은 도구의 입력 검사가 막는다."""
    from app.tools.interest import calc_interest
    args = {"principal": 10_000_000, "start_date": "2026-09-30", "maturity_date": "2026-02-01", "termination_date": "2026-10-01"}
    clean, dropped = ground_calc_args(args, ["1000만원, 2026-02-01, 2026-09-30, 2026-10-01"], TODAY_CASES)
    assert dropped == {} and calc_interest(**clean)["status"] == "invalid_input"


# ---------- 상대 날짜: 코드가 오늘 날짜로 계산한다 ----------
@pytest.mark.parametrize("field,text,value", [
    ("termination_date", "어제 해지했어요", "2026-10-27"), ("termination_date", "내일 해지하려고요", "2026-10-29"),
    ("termination_date", "다음 달 20일에 깨면 이자가 얼마예요?", "2026-11-20"), ("start_date", "작년 12월 7일에 가입했어요", "2025-12-07"),
    ("start_date", "올해 1월 1일에 가입했어요", "2026-01-01"), ("start_date", "어제 가입했어요", "2026-10-27"),
    ("start_date", "1/1에 가입했어요", "2026-01-01"), ("start_date", "20260101에 가입했어요", "2026-01-01"),
])
def test_relative_and_alternative_date_forms_are_grounded(field, text, value):
    assert dropped_of({field: value}, text) == {}


def test_maturity_next_year_is_computed_from_today():
    assert dropped_of({"maturity_date": "2027-04-02"}, "4월 2일에 가입했고 만기는 내년 4월 2일이에요") == {}


@pytest.mark.parametrize("text,field,wrong", [
    ("다음 달 20일에 깨면 이자가 얼마예요?", "termination_date", "2026-04-20"),      # 5일차 이후 실제 모델이 계산한 틀린 값 (맞는 값은 2026-11-20)
    ("다음 달 20일에 깨면 이자가 얼마예요?", "termination_date", "2026-12-20"),
    ("내일 해지하려고요", "termination_date", "2026-10-30"),
    ("작년 12월 7일에 가입했어요", "start_date", "2026-12-07"),
])
def test_a_wrong_relative_date_computed_by_the_model_is_dropped(text, field, wrong):
    assert dropped_of({field: wrong}, text) == {field: wrong}


# ---------- 칸에 낱말('오늘')을 넣은 경우 ----------
def test_a_word_in_a_date_field_is_resolved_when_the_user_said_it_and_dropped_when_not():
    clean, dropped = ground_calc_args({"termination_date": "오늘", "principal": 10_000_000}, ["1000만원이에요. 오늘 해지하면요?"], TODAY)
    assert dropped == {} and clean == {"termination_date": "2026-10-28", "principal": 10_000_000}
    clean, dropped = ground_calc_args({"termination_date": "오늘", "base_rate": 2.8, "principal": 30_000_000},
                                      ["금리 2.8%짜리 정기예금 3천만 원을 만기 전에 해지하면 이자가 어떻게 돼요?"], TODAY)
    assert dropped == {"termination_date": "오늘"} and clean == {"base_rate": 2.8, "principal": 30_000_000}       # 해지일을 말하지 않았다


# ---------- 날짜의 문맥: 해지한다고 한 날짜를 만기일 칸에 넣으면 뺀다 ----------
def test_a_date_in_the_wrong_field_is_dropped_when_the_context_is_clear():
    text = "2026-02-01에 가입하고 2026-09-30에 해지할래요"
    assert dropped_of({"maturity_date": "2026-02-01"}, text) == {"maturity_date": "2026-02-01"}          # 가입한 날을 만기일 칸에
    assert dropped_of({"start_date": "2026-09-30"}, text) == {"start_date": "2026-09-30"}                 # 해지일을 신규일 칸에
    assert dropped_of({"start_date": "2026-02-01", "termination_date": "2026-09-30"}, text) == {}         # 제 칸에 넣으면 통과
    assert dropped_of({"start_date": "2027-01-01"}, "만기: 2027-01-01") == {"start_date": "2027-01-01"}


def test_unclear_context_never_causes_a_rejection():
    """멀리 있는 키워드로 역할을 추측하지 않는다: 'A부터 B까지 넣었다가', '1년 만기로 넣었는데', '2년 만기(A ~ B)'에서 말한 값을 거절하지 않는다."""
    q = "10만원을 2026-01-01부터 2027-01-01까지 넣었다가 2026-05-31에 해지하면 이자 얼마예요?"
    assert dropped_of({"start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-05-31"}, q) == {}
    q = "2년 만기(2026-01-01 ~ 2028-01-01)로 1000만원 넣었고 기본금리는 4%예요. 2026-10-28에 깨면 얼마 받아요?"
    assert dropped_of({"start_date": "2026-01-01", "maturity_date": "2028-01-01", "termination_date": "2026-10-28", "base_rate": 4}, q) == {}
    q = "2026년 1월 1일에 천만 원을 1년 만기로 넣었는데 2026년 3월 2일에 해지하면 이자가 얼마죠?"
    assert dropped_of({"start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-03-02"}, q) == {}


def test_a_start_date_dropped_for_its_context_cannot_be_used_to_derive_the_maturity():
    """M4 회귀: 신규일이 해지 문맥('지금 해지하면') 때문에 빠졌는데, 빠지기 전의 신규일로 만기일을 '신규일 + 1년'이라고 인정했던 버그."""
    q = "5개월 전에 2000만원을 1년짜리로 가입했어요. 지금 해지하면 이자는요?"
    args = {"start_date": "2026-10-28", "maturity_date": "2027-10-28", "principal": 20_000_000}
    assert set(dropped_of(args, q)) == {"start_date", "maturity_date"}


# ---------- 기본금리와 적용금리 ----------
@pytest.mark.parametrize("text,value,kept", [
    ("기본금리 3.5%, 적용금리 4.2%였어요", 3.5, True), ("기본금리 3.5%, 적용금리 4.2%였어요", 4.2, False),
    ("쿠폰 포함해서 앱에 4.2%라고 떴어요", 4.2, False), ("우대금리 포함 4.2%예요", 4.2, False), ("적용금리는 4.2%예요", 4.2, False),
    ("금리 연 3.2%로 1,500만 원 넣었어요", 3.2, True), ("연 삼점오 퍼센트였어요", 3.5, True), ("기본금리 3.5%이고 쿠폰으로 4.2%", 3.5, True),
    ("앱에는 4.2%로 나오는데 기본금리는 3.5%래요", 3.5, True), ("앱에는 4.2%로 나오는데 기본금리는 3.5%래요", 4.2, False),
])
def test_base_rate_is_not_the_applied_rate(text, value, kept):
    assert (dropped_of({"base_rate": value}, text) == {}) is kept


@pytest.mark.parametrize("text", ["3 달 전에 넣었어요", "가입한 지 2 주 됐어요", "1 분기 지났어요"])
def test_numbers_before_calendar_units_are_not_rates(text):
    n = int(next(c for c in text if c.isdigit()))
    assert dropped_of({"base_rate": n}, text) == {"base_rate": n}


# ---------- 한글 숫자 · 기간 ----------
def test_korean_numerals_ground_terms_and_rates():
    assert dropped_of({"start_date": "2026-01-01", "maturity_date": "2027-01-01"}, "2026-01-01에 일 년 만기로 가입했어요") == {}
    assert dropped_of({"start_date": "2026-01-01", "maturity_date": "2026-07-01"}, "2026-01-01에 육 개월짜리로 가입했어요") == {}
    assert dropped_of({"base_rate": 3.5}, "기본금리는 삼점오 퍼센트예요") == {}
    assert dropped_of({"base_rate": 3.6}, "기본금리는 삼점오 퍼센트예요") == {"base_rate": 3.6}


def test_the_unsupported_approximate_dates_still_need_to_be_asked():
    assert dropped_of({"termination_date": "2026-07-31"}, "7월 말에 해지하면 이자 얼마예요?") == {"termination_date": "2026-07-31"}
    assert dropped_of({"start_date": "2026-05-28"}, "5개월 전에 가입했어요") == {"start_date": "2026-05-28"}


def test_a_coupon_alone_marks_an_applied_rate_and_the_word_basic_overrides_other_applied_words():
    assert dropped_of({"base_rate": 4.2}, "쿠폰 포함 4.2%예요") == {"base_rate": 4.2}                         # 앱 · 적용 · 우대 · 표시 없이 쿠폰만
    assert dropped_of({"base_rate": 3.5}, "앱에 표시된 기본금리는 3.5%예요") == {}                          # '기본금리'가 같이 있으면 기본금리
    assert dropped_of({"base_rate": 3.5}, "우대금리 말고 기본금리는 3.5%예요") == {}


def test_today_without_a_role_word_grounds_only_the_termination_date():
    """H20: '해지 날짜를 오늘로 잡으면' 처럼 오늘 뒤에 바로 붙은 키워드가 없어 역할을 모르는 경우."""
    q = "해지 날짜를 오늘로 잡으면 세금 떼고 실제로 얼마 들어오는지 알려줄 수 있어요?"
    assert dropped_of({"termination_date": "2026-10-28"}, q) == {}
    assert dropped_of({"start_date": "2026-10-28"}, q) == {"start_date": "2026-10-28"}
    assert dropped_of({"termination_date": "2026-10-28"}, "오늘이요") == {} and dropped_of({"maturity_date": "2026-10-28"}, "오늘이요") == {"maturity_date": "2026-10-28"}
