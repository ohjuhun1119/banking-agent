"""이자 계산 도구의 입력 검사(app/validators.py) 시험."""
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.validators import parse_date, parse_principal, parse_rate, validate_interest_inputs  # noqa: E402

OK = dict(principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-05-31")


def fields(res):
    return [e["field"] for e in res.errors]


# ---------- 값 하나를 읽는 규칙
@pytest.mark.parametrize("value,expected", [(10_000_000, 10_000_000), ("10000000", 10_000_000), ("10,000,000", 10_000_000),
                                            ("10000000원", 10_000_000), (10_000_000.0, 10_000_000), (" 1000000 ", 1_000_000)])
def test_parse_principal_accepts(value, expected):
    assert parse_principal(value) == expected


@pytest.mark.parametrize("value", [1_000_000.5, "1000만원", "일천만원", "abc", "10.5", True, [], {}])
def test_parse_principal_rejects(value):
    with pytest.raises(ValueError):
        parse_principal(value)


def test_parse_date_accepts_iso_and_date_objects():
    assert parse_date("2026-05-31") == date(2026, 5, 31)
    assert parse_date(date(2026, 5, 31)) == date(2026, 5, 31)
    assert parse_date(datetime(2026, 5, 31, 13, 0)) == date(2026, 5, 31)


@pytest.mark.parametrize("value", ["2026/05/31", "2026.05.31", "2026-5-3", "26-05-31", "20260531", "2026-02-30", "2026-13-01", "내일", 20260531, None])
def test_parse_date_rejects(value):
    with pytest.raises(ValueError):
        parse_date(value)


@pytest.mark.parametrize("value,expected", [(3.5, "3.5"), ("3.5", "3.5"), ("3.5%", "3.5"), (" 4 % ", "4"), (4, "4")])
def test_parse_rate_accepts(value, expected):
    assert parse_rate(value) == Decimal(expected)


@pytest.mark.parametrize("value", ["abc", "", float("nan"), float("inf"), True, [], "3,5"])
def test_parse_rate_rejects(value):
    with pytest.raises(ValueError):
        parse_rate(value)


def test_float_rate_is_not_drifted():
    assert parse_rate(3.5) == Decimal("3.5") and str(parse_rate(0.1)) == "0.1"      # float을 Decimal로 바로 바꾸면 0.1000000000000000055...


# ---------- 전체 검사
def test_valid_inputs_give_typed_clean_values():
    res = validate_interest_inputs(**OK)
    assert res.ok and res.clean == {"principal": 10_000_000, "start_date": date(2026, 1, 1), "maturity_date": date(2027, 1, 1),
                                    "termination_date": date(2026, 5, 31), "base_rate": None}


def test_missing_values_are_listed_not_flagged_as_errors():
    res = validate_interest_inputs()
    assert res.missing == ["principal", "start_date", "maturity_date", "termination_date"] and res.errors == [] and res.clean is None


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_values_count_as_missing(blank):
    assert validate_interest_inputs(**{**OK, "start_date": blank}).missing == ["start_date"]


def test_base_rate_is_optional():
    assert validate_interest_inputs(**OK).ok
    assert validate_interest_inputs(**OK, base_rate="3.5%").clean["base_rate"] == Decimal("3.5")


@pytest.mark.parametrize("principal", [999_999, 500_000, 0, -1_000_000])
def test_principal_below_one_million_is_rejected(principal):
    res = validate_interest_inputs(**{**OK, "principal": principal})
    assert fields(res) == ["principal"]


def test_principal_exactly_one_million_is_accepted():
    assert validate_interest_inputs(**{**OK, "principal": 1_000_000}).ok


def test_termination_before_start_is_rejected_but_same_day_is_allowed():
    assert fields(validate_interest_inputs(**{**OK, "termination_date": "2025-12-31"})) == ["termination_date"]
    assert validate_interest_inputs(**{**OK, "termination_date": "2026-01-01"}).ok


@pytest.mark.parametrize("maturity,ok", [("2026-01-31", False), ("2026-02-01", True), ("2031-01-01", True), ("2031-01-02", False)])
def test_term_must_be_between_one_month_and_five_years(maturity, ok):
    res = validate_interest_inputs(**{**OK, "maturity_date": maturity, "termination_date": "2026-01-15"})
    assert res.ok is ok and (ok or fields(res) == ["maturity_date"])


@pytest.mark.parametrize("rate,ok", [(0, False), (-1, False), (0.01, True), (20, True), (20.01, False), (25, False)])
def test_base_rate_range(rate, ok):
    res = validate_interest_inputs(**OK, base_rate=rate)
    assert res.ok is ok and (ok or fields(res) == ["base_rate"])


def test_all_problems_are_reported_together():
    res = validate_interest_inputs(principal=500_000, start_date="2026-01-01", maturity_date="2026-01-10",
                                   termination_date="2025-01-01", base_rate=25)
    assert sorted(fields(res)) == ["base_rate", "maturity_date", "principal", "termination_date"]


def test_invalid_and_missing_can_coexist():
    res = validate_interest_inputs(principal=500_000, start_date="2026-01-01")
    assert fields(res) == ["principal"] and res.missing == ["maturity_date", "termination_date"]


def test_errors_carry_label_and_message_for_the_llm():
    err = validate_interest_inputs(**{**OK, "principal": 500_000}).errors[0]
    assert err["label"] == "원금" and "최소 가입금액은 100만원입니다" in err["message"] and "500,000" in err["message"]


# ---------- 최소 가입금액 안내 문구
def test_below_minimum_error_uses_the_fixed_notice_and_a_code():
    from app.validators import MIN_DEPOSIT_NOTICE, MIN_PRINCIPAL
    err = validate_interest_inputs(**{**OK, "principal": 100_000}).errors[0]
    assert MIN_DEPOSIT_NOTICE == "최소 가입금액은 100만원입니다." and MIN_PRINCIPAL == 1_000_000
    assert err["message"].startswith(MIN_DEPOSIT_NOTICE) and "100,000" in err["message"]
    assert err["code"] == "below_min_principal" and err["value"] == 100_000


def test_below_min_message_names_the_amount_and_asks_for_a_larger_one():
    from app.validators import below_min_message
    text = below_min_message(100_000)
    assert text.startswith("최소 가입금액은 100만원입니다.") and "100,000원" in text and "100만원 이상" in text
