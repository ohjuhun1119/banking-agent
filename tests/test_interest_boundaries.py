"""중도해지 이자 계산 검증 보강 (튜터 피드백: 경계일 · 계약기간 · 최저금리 · 절사 · 만기 이후 입력).

기존 시험(test_interest.py)은 구간 이름을 '개월 수'로만 확인하고 계약기간이 1년인 사례가 대부분이라, 경과율의 분모(계약일수)를 365로 고정하는 버그가 있어도
발견하기 어려웠다. 여기서는 (1) 도구 코드를 쓰지 않는 독립 기준 공식으로 6 · 9 · 11개월이 되는 날의 전날과 당일을 계약기간별로 대조하고,
(2) 손으로 계산해 고정한 숫자를 따로 두어 두 구현이 똑같이 틀리는 경우도 막고, (3) 최저금리 · 금리와 이자의 절사 · 만기일 전후 입력을 확인한다.
"""
import calendar
import math
import sys
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools.interest import calc_interest  # noqa: E402

P = 10_000_000
BASE = 3.0


# ---------- 도구 코드를 가져오지 않고 직접 짠 기준 공식 (노션 규칙)
def ref_add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def ref_months(start: date, term: date) -> int:
    n = 0
    while ref_add_months(start, n + 1) <= term:
        n += 1
    return n


def ref(principal: int, start: date, mat: date, term: date, base) -> tuple:
    """(경과 개월, 경과일수, 계약일수, 금리 %, 이자 원). 6개월 미만은 고정 이율, 이상은 기본금리 × 차등율 × 경과일/계약일을 소수 셋째 자리 절사, 최저 0.200%."""
    months, elapsed, contract = ref_months(start, term), (term - start).days, (mat - start).days
    if months < 6:
        rate = {0: Fraction(10, 100), 1: Fraction(15, 100), 2: Fraction(15, 100)}.get(months, Fraction(20, 100))
    else:
        ratio = Fraction(60, 100) if months < 9 else Fraction(70, 100) if months < 11 else Fraction(90, 100)
        rate = Fraction(str(base)) * ratio * Fraction(min(elapsed, contract), contract)
        rate = max(Fraction(math.floor(rate * 1000), 1000), Fraction(20, 100))
    return months, elapsed, contract, rate, math.floor(Fraction(principal) * rate / 100 * elapsed / 365)


def run(start, mat, term, base=BASE, principal=P):
    return calc_interest(principal=principal, start_date=start, maturity_date=mat, termination_date=term, base_rate=base)


# 계약기간이 1년이 아닌 사례를 넣는다: 분모(계약일수)가 304 · 334 · 365 · 730 · 1096 · 1826 으로 다르다 (말일 · 윤일 가입은 365 · 730이라 겹친다)
CONTRACTS = {
    "10개월": (date(2026, 1, 1), date(2026, 11, 1)),
    "11개월": (date(2026, 1, 1), date(2026, 12, 1)),
    "1년": (date(2026, 1, 1), date(2027, 1, 1)),
    "2년": (date(2026, 1, 1), date(2028, 1, 1)),
    "3년(윤일 포함)": (date(2026, 1, 1), date(2029, 1, 1)),
    "5년(윤일 포함)": (date(2026, 1, 1), date(2031, 1, 1)),
    "말일 가입 1년": (date(2026, 8, 31), date(2027, 8, 31)),
    "윤일 가입 2년": (date(2024, 2, 29), date(2026, 2, 28)),
}


def boundary_cases() -> list[tuple]:
    out = []
    for name, (start, mat) in CONTRACTS.items():
        for months in (6, 9, 11):
            on = ref_add_months(start, months)
            for label, term in (("전날", on - timedelta(days=1)), ("당일", on)):
                if start < term < mat:                                     # 만기 전에 해지하는 날만
                    out.append(pytest.param(start, mat, term, id=f"{name}-{months}개월-{label}"))
    return out


# ---------- 1. 6 · 9 · 11개월이 되는 날의 전날과 당일, 계약기간별
def test_the_boundary_matrix_is_not_quietly_shrunk():
    ids = [p.id for p in boundary_cases()]
    assert len(ids) >= 40
    for months in (6, 9, 11):
        assert any(f"-{months}개월-전날" in i for i in ids) and any(f"-{months}개월-당일" in i for i in ids)
    assert len({(m - s).days for s, m in CONTRACTS.values()}) >= 6          # 계약일수가 서로 다른 사례가 충분하다


@pytest.mark.parametrize("start,mat,term", boundary_cases())
def test_every_boundary_day_matches_the_independent_formula(start, mat, term):
    months, elapsed, contract, rate, interest = ref(P, start, mat, term, BASE)
    r = run(start.isoformat(), mat.isoformat(), term.isoformat())
    assert r["status"] == "ok"
    assert (r["contract_days"], r["elapsed_days"]) == (contract, elapsed)
    assert Fraction(r["rate_pct"]) == rate and r["interest_krw"] == interest and r["payout_krw"] == P + interest


# 손으로 계산해 고정한 값: (신규일, 만기일, 해지일, 경과일, 계약일, 금리 %, 이자, 구간). 기본금리 3.0%, 원금 1,000만 원
ANCHORS = [
    ("2026-01-01", "2027-01-01", "2026-06-30", 180, 365, "0.200", 9863, "3개월 이상 ~ 6개월 미만"),       # 6개월 전날: 고정 이율
    ("2026-01-01", "2027-01-01", "2026-07-01", 181, 365, "0.892", 44233, "6개월 이상 ~ 9개월 미만"),     # 6개월 당일: 3.0 × 60% × 181/365
    ("2026-01-01", "2027-01-01", "2026-09-30", 272, 365, "1.341", 99932, "6개월 이상 ~ 9개월 미만"),     # 9개월 전날
    ("2026-01-01", "2027-01-01", "2026-10-01", 273, 365, "1.570", 117427, "9개월 이상 ~ 11개월 미만"),   # 9개월 당일: 3.0 × 70% × 273/365
    ("2026-01-01", "2027-01-01", "2026-11-30", 333, 365, "1.915", 174710, "9개월 이상 ~ 11개월 미만"),   # 11개월 전날
    ("2026-01-01", "2027-01-01", "2026-12-01", 334, 365, "2.470", 226021, "11개월 이상"),               # 11개월 당일: 3.0 × 90% × 334/365
    ("2026-01-01", "2027-01-01", "2026-12-31", 364, 365, "2.692", 268462, "11개월 이상"),               # 만기 전날
    ("2026-01-01", "2028-01-01", "2026-06-30", 180, 730, "0.200", 9863, "3개월 이상 ~ 6개월 미만"),     # 2년 계약
    ("2026-01-01", "2028-01-01", "2026-07-01", 181, 730, "0.446", 22116, "6개월 이상 ~ 9개월 미만"),     # 분모 730: 365로 고정하면 0.892가 된다
    ("2026-01-01", "2028-01-01", "2026-12-01", 334, 730, "1.235", 113010, "11개월 이상"),
    ("2026-01-01", "2029-01-01", "2026-07-01", 181, 1096, "0.297", 14727, "6개월 이상 ~ 9개월 미만"),    # 3년 계약, 윤일 포함
    ("2026-01-01", "2026-11-01", "2026-10-01", 273, 304, "1.885", 140987, "9개월 이상 ~ 11개월 미만"),   # 10개월 계약: 분모 304
]


@pytest.mark.parametrize("start,mat,term,elapsed,contract,rate,interest,section", ANCHORS, ids=[f"{a[0][:4]}~{a[1][:4]}-{a[2]}" for a in ANCHORS])
def test_hand_fixed_values(start, mat, term, elapsed, contract, rate, interest, section):
    r = run(start, mat, term)
    assert (r["elapsed_days"], r["contract_days"], r["rate_pct"], r["interest_krw"], r["section"]) == (elapsed, contract, rate, interest, section)


# ---------- 2. 경과율의 분모는 365가 아니라 계약일수다
def test_the_rate_follows_the_contract_days_not_a_fixed_365():
    one_year, two_years = run("2026-01-01", "2027-01-01", "2026-07-01"), run("2026-01-01", "2028-01-01", "2026-07-01")
    assert one_year["elapsed_days"] == two_years["elapsed_days"] == 181          # 경과일수가 같아도
    assert (one_year["rate_pct"], two_years["rate_pct"]) == ("0.892", "0.446")   # 계약일수(365 대 730)만큼 경과율이 달라진다
    assert two_years["interest_krw"] != one_year["interest_krw"]


def test_the_interest_still_divides_by_365_even_when_the_contract_is_not_one_year():
    """이자의 나누는 수(365)와 경과율의 분모(계약일수)는 다른 값이다: 둘을 섞으면 이 시험이 깨진다."""
    r = run("2026-01-01", "2028-01-01", "2026-07-01")
    assert r["interest_krw"] == math.floor(Fraction(P) * Fraction("0.446") / 100 * 181 / 365) == 22116


# ---------- 3. 6개월이 되는 날을 기준으로 기본금리를 요구한다
@pytest.mark.parametrize("term,status", [("2026-06-30", "ok"), ("2026-07-01", "need_base_rate"), ("2026-09-30", "need_base_rate"), ("2026-10-01", "need_base_rate"), ("2026-12-01", "need_base_rate")])
def test_base_rate_is_needed_from_the_day_six_months_are_complete(term, status):
    r = calc_interest(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date=term)
    assert r["status"] == status


# ---------- 4. 최저금리 (연 0.200%)
@pytest.mark.parametrize("base,rate,uses_floor", [(0.5, "0.200", True), (0.67, "0.200", True), (0.672, "0.200", True), (0.674, "0.200", False), (0.68, "0.202", False), (0.7, "0.208", False)])
def test_the_minimum_rate_applies_only_when_the_computed_rate_is_lower(base, rate, uses_floor):
    r = run("2026-01-01", "2027-01-01", "2026-07-01", base=base)               # 6개월 당일. 0.672는 0.19994 -> 절사 0.199라 최저 적용, 0.674는 0.20054 -> 절사 정확히 0.200이라 최저 문구 없음
    assert r["rate_pct"] == rate and any("최저" in line for line in r["formula"]) is uses_floor


# ---------- 5. 금리와 이자의 절사
def test_the_rate_is_truncated_before_it_is_used_for_the_interest():
    r = run("2026-01-01", "2027-01-01", "2026-07-01")
    assert r["rate_pct"] == "0.892"                                              # 0.8926...의 셋째 자리까지만 남긴다 (반올림하면 0.893)
    assert r["interest_krw"] == 44233 and r["interest_krw"] != 44263             # 금리를 절사하지 않으면 44,263원이 된다


def test_the_interest_is_truncated_to_the_won_even_when_the_fraction_is_nearly_one():
    r = run("2026-01-01", "2027-01-01", "2026-07-26")
    exact = Fraction(P) * Fraction(r["rate_pct"]) / 100 * r["elapsed_days"] / 365
    assert float(exact) == pytest.approx(57284.9315, abs=1e-3)
    assert r["interest_krw"] == 57284                                            # 반올림하면 57,285원


# ---------- 6. 만기일 전후와 신규일 이전 입력
@pytest.mark.parametrize("start,mat", [("2026-01-01", "2027-01-01"), ("2026-01-01", "2028-01-01")])
def test_the_day_before_maturity_is_calculated_and_the_day_of_and_after_are_not(start, mat):
    mat_date = date.fromisoformat(mat)
    before = run(start, mat, (mat_date - timedelta(days=1)).isoformat())
    _, elapsed, contract, rate, interest = ref(P, date.fromisoformat(start), mat_date, mat_date - timedelta(days=1), BASE)
    assert before["status"] == "ok" and before["elapsed_days"] == contract - 1 == elapsed
    assert Fraction(before["rate_pct"]) == rate and before["interest_krw"] == interest
    on, after = run(start, mat, mat), run(start, mat, (mat_date + timedelta(days=1)).isoformat())
    assert (on["status"], on["reason"]) == ("not_early_termination", "on_maturity")
    assert (after["status"], after["reason"]) == ("not_early_termination", "after_maturity")
    assert "interest_krw" not in on and "interest_krw" not in after                # 중도해지가 아니므로 이자를 계산하지 않는다


def test_termination_before_the_start_date_is_invalid_and_on_the_start_date_is_zero_interest():
    assert run("2026-01-01", "2027-01-01", "2025-12-31")["status"] == "invalid_input"
    same_day = run("2026-01-01", "2027-01-01", "2026-01-01")
    assert same_day["status"] == "ok" and same_day["interest_krw"] == 0 and same_day["elapsed_days"] == 0
