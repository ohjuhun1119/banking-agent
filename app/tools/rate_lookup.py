"""중도해지금리 구간 조회 도구: 가입한 지 몇 개월인지 알면 어느 구간이고 금리가 얼마인지(또는 어떤 규칙인지) 알려 준다.

7B 모델이 구간표를 잘못 읽는 일이 있었다 (개발용 Q023: 가입한 지 두 달인데 '3개월 이상 ~ 6개월 미만'). 그래서 구간을 찾는 일은 코드가 맡는다.
구간표는 이자 계산 도구(app/tools/interest.py)의 FIXED_TIERS · DIFF_TIERS를 그대로 읽는다 (표를 두 곳에 두지 않는다).
모델에게 보이는 도구가 아니다: 모델이 쓰는 도구 목록(tool_calling.TOOL_NAMES)은 그대로 두고, 질문에서 경과 기간을 읽을 수 있을 때만 코드가 부른다.

status:
  ok            구간과 금리(6개월 미만) 또는 규칙(6개월 이상)을 찾음
  missing_info  경과 개월 수가 없음
  invalid_input 경과 개월 수가 정수가 아니거나 범위(0~60개월)를 벗어남
"""
from __future__ import annotations

from decimal import Decimal

from app.tools.interest import DIFF_TIERS, FIXED_TIERS, MIN_LONG_RATE, section_label

MAX_MONTHS = 60          # 이 상품의 계약기간은 최대 5년이다 (app/validators.py MAX_TERM_MONTHS)
NAME = "lookup_termination_rate"
ASSUMPTIONS = ["사용자가 말한 기간을 '완전히 지난 개월 수'로 해석했습니다 (은행 확인 전)"]


def _fail(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


def _months(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def lookup_termination_rate(elapsed_months=None) -> dict:
    """가입 후 경과 개월 수로 중도해지금리 구간을 찾는다.

    Args:
        elapsed_months: 신규일부터 완전히 지난 개월 수 (정수, 0~60). 예: 가입한 지 두 달이면 2
    """
    if elapsed_months is None:
        return _fail("missing_info", "가입한 지 몇 개월이 지났는지 알려주세요.", missing=["경과 개월 수"])
    n = _months(elapsed_months)
    if n is None or n < 0 or n > MAX_MONTHS:
        return _fail("invalid_input", f"경과 개월 수를 다시 확인해 주세요 (0개월 이상 {MAX_MONTHS}개월 이하의 정수). 받은 값: {elapsed_months}")
    label = section_label(n)
    spoken = f"가입한 지 {n}개월이면" if n else "가입한 지 1개월이 안 됐다면"
    fixed = next((rate for limit, lb, rate in FIXED_TIERS if lb == label), None)
    if fixed is not None:                                  # 6개월 미만: 구간마다 고정 이율이라 금리까지 정해진다
        text = f"연 {fixed:.2f}%"
        return {"status": "ok", "kind": "fixed", "elapsed_months": n, "section": label, "rate_pct": str(fixed),
                "summary": f"가입 후 {n}개월: {label}, 중도해지금리 {text}(고정)",
                "formula": [f"{label}: 연 {fixed}% (고정)"], "assumptions": list(ASSUMPTIONS), "warnings": [],
                "message": f"{spoken} '{label}' 구간이라 중도해지금리는 {text}(고정)예요. 이자 금액이 궁금하면 원금과 신규일 · 만기일 · 해지일을 알려 주세요."}
    diff = next(d for limit, lb, d in DIFF_TIERS if lb == label)
    pct = int(diff * 100)
    return {"status": "ok", "kind": "differential", "elapsed_months": n, "section": label, "diff_pct": pct, "min_rate_pct": str(MIN_LONG_RATE),
            "summary": f"가입 후 {n}개월: {label}, 기본금리의 {pct}% × 경과일수/계약일수 (최저 연 {MIN_LONG_RATE:.2f}%)",
            "formula": [f"{label}: 중도해지금리 = 기본금리 × 차등율 {pct}% × 경과율(경과일수/계약일수) → 소수점 넷째 자리 절사, 최저 연 {MIN_LONG_RATE}%"],
            "assumptions": list(ASSUMPTIONS), "warnings": [],
            "message": f"{spoken} '{label}' 구간이에요. 이 구간은 가입 당시 기본금리의 {pct}%에 경과일수 ÷ 계약일수를 곱해서 정하고, 그 값이 연 {MIN_LONG_RATE:.2f}%보다 낮으면 "
                       f"연 {MIN_LONG_RATE:.2f}%를 적용해요. 정확한 금리는 기본금리와 계약기간을 알아야 해서, 원금 · 신규일 · 만기일 · 해지일 · 기본금리를 알려 주시면 계산해 드릴게요."}
