"""중도해지 이자 계산 도구 (『하나의 정기예금』 요약 상품설명서 기준, 세전).

원칙: 일수와 금액은 LLM이 아니라 이 코드가 계산한다. 입력은 날짜 그대로 받고, 계산에 쓴 가정을 결과에 함께 돌려준다.
계산에는 Fraction(정확한 분수)을 써서, 소수점 절사가 부동소수점 오차 때문에 한 단위씩 어긋나지 않게 한다.

calc_interest()는 항상 dict를 돌려주고 status로 다음 행동을 알려준다.
  ok                    계산 완료
  need_base_rate        경과 6개월 이상이라 기본금리가 필요함 (되물어야 함)
  missing_info          필요한 값이 아직 없음 (되물어야 함)
  invalid_input         값의 단위 · 범위가 틀림 (다시 확인해 달라고 해야 함)
  not_early_termination 해지일이 만기일 이후(또는 당일)라 중도해지가 아님 (계산하지 않음)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from app.tools.dates import completed_months
from app.validators import LABELS, below_min_message, validate_interest_inputs

DAY_COUNT = 365                                  # [가정] 문서에 나누는 수가 명시돼 있지 않다
RATE_STEP = Decimal("0.001")                     # 중도해지금리는 소수점 셋째 자리까지 (넷째 자리에서 절사)
MIN_LONG_RATE = Decimal("0.200")                 # 6개월 이상 구간의 최저 금리 (연 0.20%)

FIXED_TIERS = [                                  # (경과 개월 상한, 구간 이름, 연 %)
    (1, "1개월 미만", Decimal("0.100")),
    (3, "1개월 이상 ~ 3개월 미만", Decimal("0.150")),
    (6, "3개월 이상 ~ 6개월 미만", Decimal("0.200")),
]
DIFF_TIERS = [                                   # 6개월 이상: (경과 개월 상한, 구간 이름, 차등율)
    (9, "6개월 이상 ~ 9개월 미만", Decimal("0.60")),
    (11, "9개월 이상 ~ 11개월 미만", Decimal("0.70")),
    (None, "11개월 이상", Decimal("0.90")),
]

ASSUMPTIONS = [
    "이자 계산의 나누는 수는 365일로 가정했습니다 (문서에 명시 없음)",
    "경과 개월 수는 신규일에 n개월을 더한 날(해당 월에 그 날짜가 없으면 말일)이 해지일 이하인 가장 큰 n으로 판정했습니다 (은행 확인 전)",
    "경과일수는 해지일 − 신규일, 계약일수는 만기일 − 신규일로 계산했습니다 (문서에 계약일수 정의 없음)",
    "세전 기준이며 원 미만은 절사했습니다",
]
BASE_RATE_ASSUMPTION = ("입력한 기본금리를 신규일 당시 고시된 계약기간별 기본금리로 보고 계산했습니다 "
                        "(가입 시 앱에 표시된 적용금리와 다를 수 있고, 쿠폰 우대금리는 중도해지 시 적용되지 않습니다)")


class MissingBaseRate(Exception):
    """경과 6개월 이상인데 기본금리가 없을 때. 에이전트가 기본금리를 되묻게 하는 신호다."""


@dataclass(frozen=True)
class RateResult:
    rate: Decimal            # 적용되는 중도해지금리 (연, %)
    section: str             # 구간 이름
    formula: str             # 사람이 읽는 계산식
    floor_applied: bool = False


def section_label(months: int) -> str:
    for limit, label, _ in FIXED_TIERS:
        if months < limit:
            return label
    for limit, label, _ in DIFF_TIERS:
        if limit is None or months < limit:
            return label
    raise AssertionError("unreachable")


def truncate_rate(value: Decimal | Fraction) -> Decimal:
    """소수점 셋째 자리까지 남기고 넷째 자리부터 절사한다 (1.564721 -> 1.564)."""
    exact = Fraction(value)
    return Decimal(math.floor(exact / Fraction(RATE_STEP))) * RATE_STEP


def compute_rate(months: int, base_rate: Decimal | None, elapsed_days: int, contract_days: int) -> RateResult:
    for limit, label, rate in FIXED_TIERS:
        if months < limit:
            return RateResult(rate, label, f"{label}: 연 {rate}% (고정)")
    if base_rate is None:
        raise MissingBaseRate()
    label, diff = next((lb, d) for limit, lb, d in DIFF_TIERS if limit is None or months < limit)
    ratio = Fraction(min(elapsed_days, contract_days), contract_days)          # 경과일수는 계약일수를 넘을 수 없다
    raw = Fraction(base_rate) * Fraction(diff) * ratio
    rate = truncate_rate(raw)
    floor = rate < MIN_LONG_RATE
    if floor:
        rate = MIN_LONG_RATE
    raw_text = f"{Decimal(raw.numerator) / Decimal(raw.denominator):.6f}"
    formula = (f"{label}: 중도해지금리 = 기본금리 {base_rate}% × 차등율 {int(diff * 100)}% × 경과율({elapsed_days}일/{contract_days}일)"
               f" = {raw_text}% → 소수점 넷째 자리 절사 {truncate_rate(raw)}%" + (f" → 최저 연 {MIN_LONG_RATE}% 적용" if floor else ""))
    return RateResult(rate, label, formula, floor)


def early_termination_rate(months: int, base_rate: Decimal | None, elapsed_days: int, contract_days: int) -> Decimal:
    return compute_rate(months, base_rate, elapsed_days, contract_days).rate


def interest_won(principal: int, rate_pct: Decimal, days: int) -> int:
    """이자 = 원금 × 금리(%) / 100 × 일수 / 365, 원 미만 절사. 분수로 계산해 오차 없이 내림한다."""
    return int(Fraction(principal) * Fraction(rate_pct) / 100 * days / DAY_COUNT)


def _fail(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


def calc_interest(principal=None, start_date=None, maturity_date=None, termination_date=None, base_rate=None) -> dict:
    """정기예금을 만기 전에 해지할 때의 세전 이자를 계산한다.

    Args:
        principal: 원금(원 단위 정수, 100만 원 이상). 예: 10000000
        start_date: 신규일 (YYYY-MM-DD). 재예치 계좌는 최종 재예치일
        maturity_date: 만기일 (YYYY-MM-DD)
        termination_date: 해지일 (YYYY-MM-DD)
        base_rate: 신규일 당시 고시된 계약기간별 기본금리(%). 경과 6개월 이상일 때만 필요. 예: 3.5
    """
    v = validate_interest_inputs(principal, start_date, maturity_date, termination_date, base_rate)
    missing = [LABELS[f] for f in v.missing]
    below = next((e for e in v.errors if e.get("code") == "below_min_principal"), None)
    if below:                                    # 가입할 수 없는 금액이면 날짜 같은 다른 값을 더 묻지 않고 이 안내만 한다
        return _fail("invalid_input", below_min_message(below["value"]), errors=v.errors, missing=[])
    if v.errors:
        return _fail("invalid_input", "입력값을 확인해 주세요: " + "; ".join(f"{e['label']}: {e['message']}" for e in v.errors),
                     errors=v.errors, missing=missing)
    if v.missing:
        return _fail("missing_info", "계산에 필요한 값이 없습니다: " + ", ".join(missing), missing=missing)

    c = v.clean
    start, maturity, termination = c["start_date"], c["maturity_date"], c["termination_date"]
    if termination >= maturity:
        on_day = termination == maturity
        return _fail("not_early_termination",
                     ("해지일이 만기일 당일이라 만기 해지입니다." if on_day else "해지일이 만기일 이후라 중도해지가 아닙니다.")
                     + " 이 도구는 중도해지 이자만 계산합니다. 만기 해지(약정금리)나 만기 후 금리는 문서에 기준이 되는 금리 숫자가 없어 금액을 확인할 수 없습니다.",
                     reason="on_maturity" if on_day else "after_maturity")

    elapsed, contract = (termination - start).days, (maturity - start).days
    months = completed_months(start, termination)
    common = {"principal": c["principal"], "start_date": start.isoformat(), "maturity_date": maturity.isoformat(),
              "termination_date": termination.isoformat(), "elapsed_days": elapsed, "contract_days": contract,
              "completed_months": months, "section": section_label(months)}
    try:
        rr = compute_rate(months, c["base_rate"], elapsed, contract)
    except MissingBaseRate:
        return _fail("need_base_rate",
                     f"신규일부터 {months}개월이 지나 6개월 이상 구간입니다. 계산하려면 신규일 당시 홈페이지에 고시된 계약기간별 기본금리(%)가 필요합니다. "
                     "가입 시 앱에 표시된 적용금리(쿠폰 우대금리 포함)와 다를 수 있습니다.",
                     missing=[LABELS["base_rate"]], **common)

    interest = interest_won(c["principal"], rr.rate, elapsed)
    warnings, assumptions = [], list(ASSUMPTIONS)
    if months >= 6:
        assumptions.insert(0, BASE_RATE_ASSUMPTION)
        if c["base_rate"] < 1:
            warnings.append(f"기본금리가 {c['base_rate']}%로 1% 미만입니다. 퍼센트(%) 단위(예: 3.5)가 맞는지 확인하세요")
    elif c["base_rate"] is not None:
        warnings.append("기본금리는 경과 6개월 미만 구간에서 쓰이지 않아 무시했습니다")
    if elapsed == 0:
        warnings.append("신규일 당일 해지라 이자가 0원입니다")
    formula = [rr.formula, f"이자 = 원금 {c['principal']:,}원 × {rr.rate}% × {elapsed}일 / {DAY_COUNT}일 = {interest:,}원 (원 미만 절사)"]
    return {
        "status": "ok", **common, "rate_pct": str(rr.rate), "interest_krw": interest, "payout_krw": c["principal"] + interest,
        "formula": formula, "assumptions": assumptions, "warnings": warnings,
        "summary": f"{start.isoformat()} 신규, {termination.isoformat()} 해지: 경과 {elapsed}일({months}개월), {rr.section}, "
                   f"중도해지금리 연 {rr.rate}% → 세전 이자 {interest:,}원",
    }
