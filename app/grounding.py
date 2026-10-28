"""계산 도구에 넘기는 값이 '사용자가 실제로 말한 값'인지 대조한다 (값 근거 대조).

왜 필요한가: 7B 모델은 말하지 않은 값을 채워 넣는다. 5일차 측정에서 기본금리를 지어낸 경우가 많았고(short 14건, detailed 5건),
6일차 실제 대화에서도 기본금리를 말하지 않았는데 모델이 설명의 예시값(3.5)을 넣어서, 되물어야 할 질문(need_base_rate)이
틀린 근거로 계산돼 버렸다. 프롬프트로는 막히지 않아서 코드로 막는다.

규칙: 대화에서 사용자가 한 말(앞 질문 포함)에 없는 값은 도구에 넘기지 않고 비운다 -> 도구가 missing_info / need_base_rate로 되묻게 한다.
  원금        금액 표현이 같은 값 (1000만원, 천만 원, 1천만원, 10,000,000원, 10000000)
  날짜        같은 날짜 표현 (2026-01-01, 2026.1.1, 2026년 1월 1일). 연도 없는 '12월 7일'은 올해(또는 말한 날짜의 해)일 때만
              해지일은 '오늘' · '지금'이라고 했으면 오늘 날짜
              만기일은 말한 신규일에 'N년 만기' · 'N개월짜리'를 더한 날
  기본금리    사용자 말에 있는 같은 숫자 (3.5%, 3.5퍼센트, 기본금리는 3.5였어요)
  앞에서 확인된 값(state.confirmed)은 이미 근거가 확인된 값이라 그대로 인정한다.

한계: 값이 '말해졌는지'만 보고 어느 칸의 값인지는 보지 않는다 (신규일을 만기일 칸에 넣는 실수는 도구의 입력 검사가 잡는다).
      형식을 못 읽는 값(원금 '1000만원' 문자열 등)은 건드리지 않고 도구의 입력 검사가 안내하게 둔다.
      한글 숫자('이천만 원')와 '내일' · '다음 달' 같은 상대 날짜는 근거로 인정하지 않는다 (틀린 값을 쓰느니 되묻는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.tools.amounts import parse_korean_amount
from app.tools.dates import add_months
from app.validators import parse_date, parse_principal, parse_rate

FIELD_LABELS = {"principal": "원금", "start_date": "신규일", "maturity_date": "만기일", "termination_date": "해지일", "base_rate": "기본금리"}

_FULL_DATE = re.compile(r"(?<!\d)(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?(?!\d)")
_MONTH_DAY = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_TERM_PATTERNS = (re.compile(r"(\d+)\s*(년|개월)\s*(?:짜리|만기|제)"), re.compile(r"만기\s*(?:는|가|:)?\s*(\d+)\s*(년|개월)"))
_AMOUNT = re.compile(r"(?<![일이삼사오육칠팔구])(?:\d[\d,]*|[십백천만억])(?:\s?(?:\d[\d,]*|[십백천만억]))*")
_NUMBER = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?")
_UNIT_AFTER = re.compile(r"\s*(?:년|개월|월|일|주|원|만|억|천|백|십|번|회|시|분|세|살|개|명)")
_TODAY_WORDS = re.compile(r"오늘|지금(?!까지)|당장")


@dataclass
class Facts:
    """사용자 말에서 읽은 값들."""
    dates: set = field(default_factory=set)             # date
    month_days: set = field(default_factory=set)        # (월, 일): 연도 없이 말한 날짜
    amounts: set = field(default_factory=set)           # int(원)
    rates: set = field(default_factory=set)             # Decimal
    terms: list = field(default_factory=list)           # 'N년 만기' -> (N, 'year') / 'N개월짜리' -> (N, 'month')
    says_today: bool = False


def _mask(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(" ", text)


def extract_facts(text: str) -> Facts:
    text = text or ""
    f = Facts()
    for m in _FULL_DATE.finditer(text):
        try:
            f.dates.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:                                   # 2026-02-30 같은 없는 날짜는 근거가 되지 않는다
            pass
    rest = _mask(text, _FULL_DATE)
    for m in _MONTH_DAY.finditer(rest):
        f.month_days.add((int(m.group(1)), int(m.group(2))))
    rest = _mask(rest, _MONTH_DAY)
    for pat in _TERM_PATTERNS:
        for m in pat.finditer(rest):
            f.terms.append((int(m.group(1)), "year" if m.group(2) == "년" else "month"))
    f.says_today = bool(_TODAY_WORDS.search(text))
    for m in _AMOUNT.finditer(rest):
        try:
            f.amounts.add(parse_korean_amount(m.group(0)))
        except ValueError:
            pass
    for m in _NUMBER.finditer(rest):
        token = m.group(0)
        if "," in token or _UNIT_AFTER.match(rest, m.end()):
            continue                                         # 금액 · 기간(2년, 6개월) · 횟수의 숫자는 금리가 아니다
        f.rates.add(Decimal(token))
    return f


def _term_dates(start: date, terms: list) -> set:
    return {add_months(start, n * 12 if unit == "year" else n) for n, unit in terms}


def ground_calc_args(args: dict | None, texts: list[str], today: date | None = None, known: dict | None = None) -> tuple[dict, dict]:
    """계산 도구 인자 중 근거 없는 값을 뺀다. 반환: (남은 인자, 뺀 인자 {칸: 모델이 넣은 값})

    texts: 대화에서 사용자가 한 말들. known: 앞에서 확인된 값 (원금 int, 날짜 'YYYY-MM-DD', 기본금리 float).
    """
    args = dict(args or {})
    today = today or date.today()
    known = known or {}
    facts = extract_facts("\n".join(t for t in texts if isinstance(t, str)))
    years = {today.year} | {d.year for d in facts.dates}

    def known_value(fld):
        if fld not in known:
            return None
        try:
            return {"principal": parse_principal, "base_rate": parse_rate}.get(fld, parse_date)(known[fld])
        except ValueError:
            return None

    def parsed(fld):
        """인자를 읽는다. 비었거나 형식을 못 읽으면 None (건드리지 않고 도구의 입력 검사에 맡긴다)."""
        v = args.get(fld)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return {"principal": parse_principal, "base_rate": parse_rate}.get(fld, parse_date)(v)
        except ValueError:
            return None

    def said_date(v: date) -> bool:
        return v in facts.dates or ((v.month, v.day) in facts.month_days and v.year in years)

    dropped: dict = {}
    start = parsed("start_date")
    start_ok = start is not None and (said_date(start) or start == known_value("start_date"))
    base_start = start if start_ok else (known_value("start_date") if start is None else None)    # 만기일을 계산할 기준 신규일
    for fld in ("principal", "start_date", "maturity_date", "termination_date", "base_rate"):
        v = parsed(fld)
        if v is None:
            continue
        if fld == "principal":
            ok = v in facts.amounts
        elif fld == "base_rate":
            ok = v in facts.rates
        elif fld == "start_date":
            ok = start_ok
        elif fld == "maturity_date":
            ok = said_date(v) or (base_start is not None and v in _term_dates(base_start, facts.terms))
        else:                                                # termination_date
            ok = said_date(v) or (facts.says_today and v == today)
        if not ok and v != known_value(fld):
            dropped[fld] = args[fld]
    return {k: v for k, v in args.items() if k not in dropped}, dropped


def describe_dropped(dropped: dict) -> str:
    return ", ".join(f"{FIELD_LABELS.get(k, k)} {v}" for k, v in dropped.items())
