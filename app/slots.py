"""사용자가 질문에 적은 값을 읽어, 모델이 계산 도구에 빠뜨린 칸을 채운다.

왜 필요한가: 5일차 측정(missing_arg)과 검증용 20문항 실제 모델 결과에서, 모델은 질문에 적힌 만기일을 계산 도구에 넣지 않았다
(값이 충분했던 3문항 모두 만기일을 또 물었고 계산 단계까지 가지 못했다). 문구를 바꿔도 줄지 않아서 코드로 채운다.
값 근거 대조(grounding)가 '말하지 않은 값을 빼는' 쪽이라면, 이것은 '말한 값을 빠뜨린 칸을 채우는' 쪽이다.

규칙 (틀린 값을 채우느니 비워 둔다: 확실한 것만)
  날짜   역할이 분명한 날짜만: 날짜 바로 앞의 라벨('만기는 내년 4월 2일')이나 바로 뒤의 키워드('2025년 6월 1일에 들었고', '오늘 해지하면').
         같은 역할로 읽힌 날짜가 둘 이상이면 채우지 않는다. 신규일이 오늘보다 뒤인 경우도 채우지 않는다.
  만기일  말한 신규일에 'N년 만기' · 'N개월짜리'를 더한 날 (역할이 분명한 만기일이 없을 때)
  원금   100만 원 이상 금액이 한 문장에 정확히 하나일 때만 (금액이 둘이면 어느 것이 원금인지 모른다)
  기본금리  '기본금리는 3.5%'처럼 기본금리라고 말한 것만 (그냥 '금리 연 3%'는 적용금리일 수 있어 채우지 않는다)
모델이 이미 넣은 값은 덮어쓰지 않는다. 문장(메시지)마다 따로 읽고, 나중 문장이 앞 문장을 덮어쓴다.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.grounding import extract_facts
from app.korean_numbers import normalize_numerals
from app.relative_dates import ROLE_FIELD, date_mentions
from app.tools.dates import add_months

_BASE_RATE = re.compile(r"기본\s?금리\s*(?:는|가|은|이|:)?\s*(?:연\s*)?(\d+(?:\.\d+)?)\s*(?:%|퍼센트|프로)")
MIN_PRINCIPAL = 1_000_000


def message_slots(text: str, today: date) -> dict:
    """한 문장에서 확실하게 읽을 수 있는 값 {칸: 값}."""
    t = normalize_numerals(text or "")
    mentions = date_mentions(t, today)
    slots: dict = {}
    for role, fld in ROLE_FIELD.items():
        cands = {m.date for m in mentions if m.role == role}
        if len(cands) == 1:
            slots[fld] = cands.pop()
    if "start_date" in slots and slots["start_date"] > today:        # 아직 오지 않은 날을 가입일이라고 읽지 않는다
        del slots["start_date"]
    facts = extract_facts(text, today)
    big = {a for a in facts.amounts if a >= MIN_PRINCIPAL}
    if len(big) == 1:
        slots["principal"] = big.pop()
    rates = {Decimal(x) for x in _BASE_RATE.findall(t)}
    if len(rates) == 1:
        slots["base_rate"] = float(rates.pop())
    terms = set(facts.terms)
    if "maturity_date" not in slots and "start_date" in slots and len(terms) == 1:
        n, unit = next(iter(terms))
        slots["maturity_date"] = add_months(slots["start_date"], n * 12 if unit == "year" else n)
    return slots


def slots_from_texts(texts: list[str], today: date) -> dict:
    """대화의 사용자 말 전체에서 읽은 값. 나중 문장이 앞 문장을 덮어쓴다."""
    merged: dict = {}
    for t in texts:
        if isinstance(t, str):
            merged.update(message_slots(t, today))
    return merged


def _json(value):
    return value.isoformat() if isinstance(value, date) else value


def fill_missing_from_text(args: dict | None, texts: list[str], today: date | None = None) -> tuple[dict, dict]:
    """계산 도구 인자 중 비어 있는 칸을 사용자가 말한 값으로 채운다. 반환: (채운 인자, 채운 값 {칸: 값}). 모델이 넣은 값은 건드리지 않는다."""
    args = dict(args or {})
    filled: dict = {}
    for fld, value in slots_from_texts(texts, today or date.today()).items():
        cur = args.get(fld)
        if cur is None or (isinstance(cur, str) and not cur.strip()):
            args[fld] = _json(value)
            filled[fld] = _json(value)
    return args, filled
