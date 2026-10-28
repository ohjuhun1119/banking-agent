"""중도해지 이자 계산 도구(app/tools/interest.py) 시험. 노션의 테스트 케이스 A~G, 금리 절사, 입력 오류, 상태 코드를 확인한다."""
import json
import sys
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools.dates import add_months, completed_months  # noqa: E402
from app.tools.interest import (MissingBaseRate, calc_interest, early_termination_rate, interest_won, section_label,  # noqa: E402
                                truncate_rate)
from app.tools.interest_cases import CASES  # noqa: E402

ARGS = ("principal", "start_date", "maturity_date", "termination_date", "base_rate")
P = 10_000_000


def run(**kw):
    base = dict(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-05-31")
    return calc_interest(**{**base, **kw})


# ---------- 노션 테스트 케이스 A~G
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_notion_cases(case):
    r = calc_interest(**{k: case[k] for k in ARGS})
    assert r["status"] == "ok"
    assert r["interest_krw"] == case["interest"]
    assert Decimal(r["rate_pct"]) == Decimal(case["rate_pct"])
    assert r["elapsed_days"] == case["elapsed_days"]
    assert r["section"] == case["section"]
    assert r["payout_krw"] == case["principal"] + case["interest"]


def test_case_g_uses_the_minimum_rate_and_a_leap_year_contract():
    r = calc_interest(**{k: CASES[6][k] for k in ARGS})
    assert r["contract_days"] == 1826                                  # 2028년 윤일 포함
    assert any("최저" in line for line in r["formula"]) and r["rate_pct"] == "0.200"


# ---------- 금리 절사 (소수점 넷째 자리부터 버림)
@pytest.mark.parametrize("value,expected", [("1.564721", "1.564"), ("1.9999", "1.999"), ("0.0009", "0.000"), ("2.934246", "2.934"), ("1.5", "1.500")])
def test_truncate_rate(value, expected):
    assert truncate_rate(Decimal(value)) == Decimal(expected)


def test_truncate_rate_is_exact_on_a_boundary():
    assert truncate_rate(Fraction(1564, 1000)) == Decimal("1.564")       # 딱 떨어지는 값이 한 단위 내려가면 안 된다


def test_fixed_tiers_below_six_months():
    assert [early_termination_rate(m, None, 10, 365) for m in (0, 1, 2, 3, 5)] == [Decimal(x) for x in ("0.100", "0.150", "0.150", "0.200", "0.200")]


@pytest.mark.parametrize("months", [6, 9, 11, 20])
def test_six_months_or_more_need_a_base_rate(months):
    with pytest.raises(MissingBaseRate):
        early_termination_rate(months, None, 200, 365)


def test_elapsed_days_are_capped_at_contract_days():
    assert early_termination_rate(11, Decimal("4"), 400, 365) == Decimal("3.600")      # 4 × 90% × (365/365)


# ---------- 계산 흐름과 상태 코드
def test_six_months_without_base_rate_asks_for_it_and_keeps_the_partial_result():
    r = run(termination_date="2026-07-20")
    assert r["status"] == "need_base_rate" and r["missing"] == ["기본금리"]
    assert r["completed_months"] == 6 and r["section"] == "6개월 이상 ~ 9개월 미만" and r["elapsed_days"] == 200
    assert "기본금리" in r["message"] and "적용금리" in r["message"]
    assert run(termination_date="2026-07-20", base_rate=3.5)["status"] == "ok"


def test_base_rate_is_ignored_below_six_months_with_a_warning():
    with_rate, without = run(base_rate=3.5), run()
    assert with_rate["interest_krw"] == without["interest_krw"] == 8219
    assert any("무시" in w for w in with_rate["warnings"]) and without["warnings"] == []


def test_suspiciously_small_base_rate_triggers_a_unit_warning():
    r = run(termination_date="2026-07-20", base_rate=0.035)               # 3.5를 0.035로 잘못 넘긴 경우
    assert r["status"] == "ok" and any("단위" in w for w in r["warnings"])


def test_nothing_given_asks_for_every_required_value():
    r = calc_interest()
    assert r["status"] == "missing_info" and r["missing"] == ["원금", "신규일", "만기일", "해지일"]


def test_invalid_values_take_priority_over_missing_ones():
    r = calc_interest(principal="천만원")                                  # 형식이 틀린 값 + 아직 못 받은 날짜들
    assert r["status"] == "invalid_input" and r["errors"][0]["field"] == "principal" and "만기일" in r["missing"]


def test_no_calculation_runs_on_invalid_input():
    r = run(principal=500_000)
    assert r["status"] == "invalid_input" and "interest_krw" not in r


@pytest.mark.parametrize("termination,reason", [("2027-01-01", "on_maturity"), ("2027-03-01", "after_maturity")])
def test_termination_on_or_after_maturity_is_not_early_termination(termination, reason):
    r = run(termination_date=termination)
    assert r["status"] == "not_early_termination" and r["reason"] == reason and "interest_krw" not in r


def test_same_day_termination_gives_zero_interest_with_a_warning():
    r = run(termination_date="2026-01-01")
    assert r["status"] == "ok" and r["interest_krw"] == 0 and r["elapsed_days"] == 0 and any("0원" in w for w in r["warnings"])


def test_exact_integer_results_are_not_rounded_down_by_float_error():
    r = run(principal=36_500_000, termination_date="2026-01-11")           # 36,500,000 × 0.100% × 10일 / 365 = 정확히 1,000원
    assert r["interest_krw"] == 1000 and interest_won(36_500_000, Decimal("0.100"), 10) == 1000


def test_interest_is_truncated_not_rounded():
    assert interest_won(P, Decimal("0.200"), 150) == 8219                  # 8219.18...
    assert interest_won(P, Decimal("1.150"), 200) == 63013                 # 63013.69...는 반올림하면 63,014가 된다


def test_result_explains_itself():
    r = run(termination_date="2026-07-20", base_rate=3.5)
    text = " ".join(r["formula"])
    assert "10,000,000" in text and "63,013" in text and "1.150" in text
    joined = " ".join(r["assumptions"])
    assert "365" in joined and "기본금리" in joined and "개월" in joined
    assert "63,013원" in r["summary"]


@pytest.mark.parametrize("kwargs", [{}, {"principal": 1}, {"termination_date": "2026-07-20"}, {"termination_date": "2027-02-01"},
                                    {"termination_date": "2026-07-20", "base_rate": 3.5}, {"termination_date": "2026-05-31"}])
def test_every_result_is_json_serializable(kwargs):
    json.dumps(calc_interest(**({"principal": P, "start_date": "2026-01-01", "maturity_date": "2027-01-01", **kwargs} if kwargs else {})))


def test_accepts_dates_as_objects_and_strings_alike():
    a = calc_interest(P, date(2026, 1, 1), date(2027, 1, 1), date(2026, 5, 31))
    assert a == run()


# ---------- 개월 수 판정 [가정: 은행 확인 전] 신규일에 n개월을 더한 날(말일 보정)이 해지일 이하인 최대 n
@pytest.mark.parametrize("start,end,expected", [
    ("2026-01-01", "2026-01-31", 0), ("2026-01-01", "2026-02-01", 1), ("2026-01-01", "2026-03-31", 2), ("2026-01-01", "2026-04-01", 3),
    ("2026-01-01", "2026-06-30", 5), ("2026-01-01", "2026-07-01", 6), ("2026-01-01", "2026-09-30", 8), ("2026-01-01", "2026-10-01", 9),
    ("2026-01-01", "2026-11-30", 10), ("2026-01-01", "2026-12-01", 11), ("2026-01-01", "2027-01-01", 12),
    ("2026-01-31", "2026-02-27", 0), ("2026-01-31", "2026-02-28", 1), ("2026-03-31", "2026-04-29", 0), ("2026-03-31", "2026-04-30", 1),
    ("2024-02-29", "2025-02-27", 11), ("2024-02-29", "2025-02-28", 12), ("2026-05-15", "2026-05-15", 0),
])
def test_completed_months_assumption(start, end, expected):
    assert completed_months(date.fromisoformat(start), date.fromisoformat(end)) == expected


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


@pytest.mark.parametrize("months,label", [(0, "1개월 미만"), (1, "1개월 이상 ~ 3개월 미만"), (2, "1개월 이상 ~ 3개월 미만"), (3, "3개월 이상 ~ 6개월 미만"),
                                           (5, "3개월 이상 ~ 6개월 미만"), (6, "6개월 이상 ~ 9개월 미만"), (8, "6개월 이상 ~ 9개월 미만"),
                                           (9, "9개월 이상 ~ 11개월 미만"), (10, "9개월 이상 ~ 11개월 미만"), (11, "11개월 이상"), (30, "11개월 이상")])
def test_section_boundaries(months, label):
    assert section_label(months) == label


def test_rate_changes_on_the_exact_month_boundary_day():
    day_before, on_day = run(termination_date="2026-01-31"), run(termination_date="2026-02-01")
    assert (day_before["rate_pct"], on_day["rate_pct"]) == ("0.100", "0.150")


# ---------- 최소 가입금액 안내: '10만원 넣으면 이자 얼마야?'
def test_below_minimum_principal_gets_the_notice_and_no_other_questions():
    r = calc_interest(principal=100_000)                                   # 날짜가 하나도 없어도 날짜를 되묻지 않는다
    assert r["status"] == "invalid_input" and r["message"].startswith("최소 가입금액은 100만원입니다.")
    assert r["missing"] == [] and r["errors"][0]["code"] == "below_min_principal"
    assert "interest_krw" not in r


def test_below_minimum_notice_does_not_depend_on_the_other_values():
    r = run(principal=100_000, termination_date="2026-07-20")
    assert r["status"] == "invalid_input" and "최소 가입금액은 100만원입니다." in r["message"] and r["missing"] == []


def test_other_invalid_values_keep_the_generic_message():
    r = run(termination_date="2025-12-31")
    assert r["status"] == "invalid_input" and "최소 가입금액" not in r["message"] and r["message"].startswith("입력값을 확인해 주세요")
