"""계산 도구에 넘기는 값이 '사용자가 실제로 말한 값'인지 대조한다 (값 근거 대조).

왜 필요한가: 7B 모델은 말하지 않은 값을 채워 넣는다. 5일차 측정에서 기본금리를 지어낸 경우가 많았고(short 14건, detailed 5건),
6일차 실제 대화에서도 기본금리를 말하지 않았는데 모델이 설명의 예시값(3.5)을 넣어서, 되물어야 할 질문(need_base_rate)이
틀린 근거로 계산돼 버렸다. 프롬프트로는 막히지 않아서 코드로 막는다.

규칙: 대화에서 사용자가 한 말(앞 질문 포함)에 없는 값은 도구에 넘기지 않고 비운다 -> 도구가 missing_info / need_base_rate로 되묻게 한다.
  원금        금액 표현이 같은 값 (1000만원, 천만 원, 1천만원, 일천만원, 1.5억, 10,000,000원, 10000000)
  날짜        같은 날짜 표현 (2026-01-01, 2026년 1월 1일, 1/1, 20260101). 연도 없는 '12월 7일'은 올해(또는 말한 날짜의 해)일 때만.
              상대 날짜는 코드가 오늘 날짜로 계산한다 (어제 · 내일 · 내년 4월 2일 · 다음 달 20일, app/relative_dates.py)
              해지일은 '오늘' · '지금'이라고 했으면 오늘 날짜
              만기일은 말한 신규일에 'N년 만기' · 'N개월짜리'를 더한 날
              날짜는 말한 문맥과 칸이 맞아야 한다 (해지한다고 한 날짜를 만기일 칸에 넣으면 뺀다)
  기본금리    사용자 말에 있는 같은 숫자 (3.5%, 삼점오 퍼센트, 기본금리는 3.5였어요). 적용금리 · 우대 · 쿠폰 · 앱에 표시된 금리는 기본금리가 아니다
  앞에서 확인된 값(state.confirmed)은 이미 근거가 확인된 값이라 그대로 인정한다.
모델이 칸에 '오늘' 같은 낱말을 넣으면: 사용자가 말했으면 오늘 날짜로 바꿔 주고, 말하지 않았으면 뺀다.
읽을 수 없는 값(원금 '1000만원' 문자열 등)은 사용자 말에 그 글자가 있으면 도구의 입력 검사가 안내하게 두고, 없으면 지어낸 값이므로 뺀다.

한계: 값이 '말해졌는지'와 날짜의 문맥만 본다. 한글 숫자는 금액 · 금리 · 기간만 읽고, '7월 말' · '3개월 전' 같은 대략적인 날짜는 인정하지 않는다
      (틀린 값을 쓰느니 되묻는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.korean_numbers import normalize_numerals
from app.relative_dates import FIELD_ROLE, RELATIVE_WORDS, date_mentions, mask_mentions, resolve_word
from app.tools.amounts import parse_korean_amount
from app.tools.dates import add_months
from app.validators import parse_date, parse_principal, parse_rate

FIELD_LABELS = {"principal": "원금", "start_date": "신규일", "maturity_date": "만기일", "termination_date": "해지일", "base_rate": "기본금리"}
DATE_FIELDS = ("start_date", "maturity_date", "termination_date")

_TERM_PATTERNS = (re.compile(r"(\d+)\s*(년|개월|달)\s*(?:짜리|만기|제)"), re.compile(r"만기\s*(?:는|가|:)?\s*(\d+)\s*(년|개월|달)"))
_DEC_AMOUNT = re.compile(r"(?<![\d.,])(\d+\.\d+)\s?(억|천만|백만|십만|만|천)(?:\s?원)?")
_DEC_MULT = {"억": 10**8, "천만": 10**7, "백만": 10**6, "십만": 10**5, "만": 10**4, "천": 10**3}
_AMOUNT = re.compile(r"(?<![일이삼사오육칠팔구])(?:\d[\d,]*|[십백천만억])(?:\s?(?:\d[\d,]*|[십백천만억]))*")
_NUMBER = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?")
_UNIT_AFTER = re.compile(r"\s*(?:년|개월|월|일|주|달|분기|원|만|억|천|백|십|번|회|시|분|세|살|개|명)")
_APPLIED_WORDS = re.compile(r"적용|우대|쿠폰|앱|표시")


@dataclass
class Facts:
    """사용자 말에서 읽은 값들."""
    dates: set = field(default_factory=set)             # date: 연도까지 말했거나 상대 날짜를 계산한 날짜
    month_days: set = field(default_factory=set)        # (월, 일): 연도 없이 말한 날짜
    roles: dict = field(default_factory=dict)           # date -> {'start' | 'maturity' | 'termination'}: 문맥에서 읽은 역할
    md_roles: dict = field(default_factory=dict)        # (월, 일) -> 역할 집합
    amounts: set = field(default_factory=set)           # int(원)
    rates: set = field(default_factory=set)             # Decimal: 모든 금리 숫자
    rates_base: set = field(default_factory=set)        # 적용금리 · 우대 · 쿠폰 문맥이 아닌 금리 (기본금리 후보)
    terms: list = field(default_factory=list)           # 'N년 만기' -> (N, 'year') / 'N개월짜리' -> (N, 'month')
    says_today: bool = False


def extract_facts(text: str, today: date | None = None, require_digit: bool = False, normalize: bool = True) -> Facts:
    """require_digit=True면 숫자가 없는 금액 표현('만기'의 '만')은 금액으로 읽지 않고, normalize=False면 한글 숫자를 바꿔 읽지 않는다 (둘 다 답변 점검용)."""
    today = today or date.today()
    text = (normalize_numerals(text or "") if normalize else (text or ""))
    f = Facts()
    mentions = date_mentions(text, today)
    for m in mentions:
        if m.kind == "monthday":
            f.month_days.add(m.month_day)
            if m.role:
                f.md_roles.setdefault(m.month_day, set()).add(m.role)
            continue
        if m.kind == "relative" and m.role is None and m.date == today:
            f.says_today = True                              # 역할 없는 '오늘' · '지금'은 날짜가 아니라 해지일 후보로만 쓴다
            continue
        f.dates.add(m.date)
        if m.date == today and m.kind == "relative":
            f.says_today = True
        if m.role:
            f.roles.setdefault(m.date, set()).add(m.role)
    rest = mask_mentions(text, mentions)
    for pat in _TERM_PATTERNS:
        for m in pat.finditer(rest):
            f.terms.append((int(m.group(1)), "year" if m.group(2) == "년" else "month"))
    for m in _DEC_AMOUNT.finditer(rest):
        f.amounts.add(int(Decimal(m.group(1)) * _DEC_MULT[m.group(2)]))
    rest = _DEC_AMOUNT.sub(lambda m: " " * len(m.group(0)), rest)
    for m in _AMOUNT.finditer(rest):
        if require_digit and not any(c.isdigit() for c in m.group(0)):
            continue
        try:
            f.amounts.add(parse_korean_amount(m.group(0)))
        except ValueError:
            pass
    for m in _NUMBER.finditer(rest):
        token = m.group(0)
        if "," in token or _UNIT_AFTER.match(rest, m.end()):
            continue                                         # 금액 · 기간 · 횟수의 숫자는 금리가 아니다
        value = Decimal(token)
        f.rates.add(value)
        window = rest[max(0, m.start() - 10):m.start()]
        if not _APPLIED_WORDS.search(window) or "기본" in window:
            f.rates_base.add(value)
    return f


def _term_dates(start: date, terms: list) -> set:
    return {add_months(start, n * 12 if unit == "year" else n) for n, unit in terms}


def ground_calc_args(args: dict | None, texts: list[str], today: date | None = None, known: dict | None = None) -> tuple[dict, dict]:
    """계산 도구 인자 중 근거 없는 값을 뺀다. 반환: (남은 인자, 뺀 인자 {칸: 모델이 넣은 값})

    texts: 대화에서 사용자가 한 말들. known: 앞에서 확인된 값 (원금 int, 날짜 'YYYY-MM-DD', 기본금리 float).
    '오늘' 같은 낱말을 칸에 넣었고 사용자가 말했다면 남은 인자에는 계산한 날짜로 바뀌어 들어간다.
    """
    original = dict(args or {})
    args = dict(original)
    today = today or date.today()
    known = known or {}
    joined = "\n".join(t for t in texts if isinstance(t, str))
    facts = extract_facts(joined, today)
    text_norm = normalize_numerals(joined)
    years = {today.year} | {d.year for d in facts.dates}

    for fld in DATE_FIELDS:                                  # '오늘' 같은 낱말은 코드가 날짜로 바꾼다
        v = args.get(fld)
        if isinstance(v, str) and v.strip() in RELATIVE_WORDS:
            args[fld] = resolve_word(v, today).isoformat()

    def parser(fld):
        return {"principal": parse_principal, "base_rate": parse_rate}.get(fld, parse_date)

    def known_value(fld):
        if fld not in known:
            return None
        try:
            return parser(fld)(known[fld])
        except ValueError:
            return None

    unreadable: dict = {}

    def parsed(fld):
        """인자를 읽는다. 비었으면 None. 형식을 못 읽으면 None이고 unreadable에 적어 둔다."""
        v = args.get(fld)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return parser(fld)(v)
        except ValueError:
            unreadable[fld] = v
            return None

    def said_date(v: date) -> bool:
        return v in facts.dates or ((v.month, v.day) in facts.month_days and v.year in years)

    def roles_of(v: date) -> set:
        return facts.roles.get(v, set()) | facts.md_roles.get((v.month, v.day), set())

    def conflicts(fld: str, v: date) -> bool:                # 말한 문맥(해지한다고 한 날짜 등)과 칸이 다르다
        roles = roles_of(v)
        return bool(roles) and FIELD_ROLE[fld] not in roles

    dropped: dict = {}
    start = parsed("start_date")
    start_ok = start is not None and ((said_date(start) and not conflicts("start_date", start)) or start == known_value("start_date"))
    base_start = start if start_ok else (known_value("start_date") if start is None and "start_date" not in unreadable else None)
    for fld in ("principal", "start_date", "maturity_date", "termination_date", "base_rate"):
        v = parsed(fld)
        if v is None:
            continue
        derived = fld == "maturity_date" and base_start is not None and v in _term_dates(base_start, facts.terms)
        if fld == "principal":
            ok = v in facts.amounts
        elif fld == "base_rate":
            ok = v in facts.rates_base
        elif fld == "start_date":
            ok = start_ok
        elif fld == "maturity_date":
            ok = said_date(v) or derived
        else:                                                # termination_date
            ok = said_date(v) or (facts.says_today and v == today)
        if ok and fld in DATE_FIELDS and not derived and conflicts(fld, v):     # 날짜는 말한 문맥과 칸이 맞아야 한다
            ok = False
        if not ok and v != known_value(fld):
            dropped[fld] = original[fld]
    for fld, raw in unreadable.items():                      # 읽을 수 없는 값: 사용자 말에 그 글자가 있으면 도구가 안내하고, 없으면 지어낸 값
        if str(raw).strip() not in joined and str(raw).strip() not in text_norm:
            dropped[fld] = original[fld]
    return {k: v for k, v in args.items() if k not in dropped}, dropped


def describe_dropped(dropped: dict) -> str:
    """뺀 값의 칸 이름만 돌려준다 (값은 말하지 않는다: 모델이 지어낸 값을 따라 말하지 않게)."""
    return ", ".join(FIELD_LABELS.get(k, k) for k in dropped)
