"""이자 계산 도구의 입력값 검사.

LLM이 단위나 범위가 틀린 값을 넘겨도 계산이 실행되지 않게, 코드가 먼저 확인한다.
  missing  아직 받지 못한 값 (되물어야 함)
  errors   받았지만 단위 · 범위가 틀린 값 (다시 확인해 달라고 해야 함)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.tools.dates import add_months

MIN_PRINCIPAL = 1_000_000            # 최소 가입금액 100만 원
MIN_TERM_MONTHS, MAX_TERM_MONTHS = 1, 60     # 계약기간 1개월 ~ 5년
MAX_BASE_RATE = Decimal("20")        # 상식 범위 (0 초과 ~ 20% 이하)
LABELS = {"principal": "원금", "start_date": "신규일", "maturity_date": "만기일",
          "termination_date": "해지일", "base_rate": "기본금리"}
REQUIRED = ("principal", "start_date", "maturity_date", "termination_date")      # 기본금리는 경과 6개월 이상일 때만 필요하다


@dataclass
class ValidationResult:
    clean: dict | None = None
    missing: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.errors


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def parse_principal(v) -> int:
    if isinstance(v, bool):
        raise ValueError("원금은 숫자여야 합니다")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not v.is_integer():
            raise ValueError("원금은 원 단위 정수여야 합니다 (소수점 불가)")
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").removesuffix("원").strip()
        if re.fullmatch(r"\d+", s):
            return int(s)
        raise ValueError("원금은 원 단위 숫자로 입력해야 합니다. 예: 10000000 ('1000만원'이 아니라 10000000)")
    raise ValueError("원금은 숫자여야 합니다")


def parse_date(v) -> date:
    if isinstance(v, datetime):                 # datetime은 date의 하위 클래스라 먼저 확인한다
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
        try:
            return date.fromisoformat(v.strip())
        except ValueError:
            raise ValueError("존재하지 않는 날짜입니다") from None
    raise ValueError("날짜는 YYYY-MM-DD 형식으로 입력해야 합니다. 예: 2026-05-31")


def parse_rate(v) -> Decimal:
    if isinstance(v, bool):
        raise ValueError("기본금리는 숫자(%)여야 합니다")
    try:
        if isinstance(v, (int, float, Decimal)):
            d = Decimal(str(v))                 # float을 그대로 Decimal로 바꾸면 3.5가 3.49999...가 될 수 있어 문자열을 거친다
        elif isinstance(v, str):
            d = Decimal(v.strip().rstrip("%").strip())
        else:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError("기본금리는 숫자(%)로 입력해야 합니다. 예: 3.5") from None
    if not d.is_finite():
        raise ValueError("기본금리는 숫자(%)로 입력해야 합니다. 예: 3.5")
    return d


def validate_interest_inputs(principal=None, start_date=None, maturity_date=None, termination_date=None,
                             base_rate=None) -> ValidationResult:
    raw = {"principal": principal, "start_date": start_date, "maturity_date": maturity_date,
           "termination_date": termination_date, "base_rate": base_rate}
    parsers = {"principal": parse_principal, "start_date": parse_date, "maturity_date": parse_date,
               "termination_date": parse_date, "base_rate": parse_rate}
    parsed: dict = {}
    res = ValidationResult()

    def err(fld: str, msg: str) -> None:
        res.errors.append({"field": fld, "label": LABELS[fld], "message": msg})

    for fld, value in raw.items():
        if _blank(value):
            if fld in REQUIRED:
                res.missing.append(fld)
            continue
        try:
            parsed[fld] = parsers[fld](value)
        except ValueError as e:
            err(fld, str(e))

    if "principal" in parsed and parsed["principal"] < MIN_PRINCIPAL:
        err("principal", f"원금은 100만 원 이상이어야 합니다 (입력: {parsed['principal']:,}원)")
        parsed.pop("principal")
    if "base_rate" in parsed and not (0 < parsed["base_rate"] <= MAX_BASE_RATE):
        err("base_rate", f"기본금리는 0% 초과 20% 이하여야 합니다 (입력: {parsed['base_rate']}%)")
        parsed.pop("base_rate")

    start, maturity, termination = (parsed.get(k) for k in ("start_date", "maturity_date", "termination_date"))
    if start and maturity:
        if maturity < add_months(start, MIN_TERM_MONTHS):
            err("maturity_date", "만기일은 신규일로부터 1개월 이상 뒤여야 합니다")
        elif maturity > add_months(start, MAX_TERM_MONTHS):
            err("maturity_date", "만기일은 신규일로부터 5년 이하여야 합니다")
    if start and termination and termination < start:
        err("termination_date", "해지일은 신규일 이후여야 합니다 (해지일이 신규일보다 앞섭니다)")

    if res.ok:
        res.clean = {"principal": parsed["principal"], "start_date": start, "maturity_date": maturity,
                     "termination_date": termination, "base_rate": parsed.get("base_rate")}
    return res
