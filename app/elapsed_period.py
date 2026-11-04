"""질문에서 '가입한 지 N개월 됐다'는 경과 기간을 읽고, 구간 조회 도구를 코드가 먼저 부를 질문인지 판별한다.

'만기까지 두 달 남았는데'처럼 남은 기간은 경과 기간이 아니므로 읽지 않는다 (검증용 X014: 남은 기간을 경과 기간으로 혼동한 오답).
'다음 달에 해지하면' · 날짜가 적힌 질문처럼 지금 시점이 아닌 질문도 읽지 않는다: 그런 질문은 이전처럼 흘러간다.
낱말 규칙이라 표현이 달라지면 놓칠 수 있다 (놓친 질문은 예전과 같은 길로 간다).
"""
from __future__ import annotations

import re

from app.evidence import calc_intent

MAX_MONTHS = 60
_NATIVE = {"한": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12}
_NUM = r"(?:\d{1,2}|열두|열한|여덟|여섯|다섯|일곱|아홉|열|한|두|세|석|네|넉)"
_PERIOD = rf"(?:{_NUM}\s*년(?:\s*반)?(?:\s*{_NUM}\s*(?:개월|달))?|반\s*년|{_NUM}\s*(?:개월|달))"
_ELAPSED = [                      # 가입한 지 두 달 됐는데 / 넣은 지 6개월 지났는데 / 두 달 전에 가입했는데
    re.compile(rf"(?:가입|넣|든|들|예치|개설|시작|신규)[가-힣]{{0,2}}\s*지\s*(?:약\s*|대략\s*|벌써\s*)?(?P<p>{_PERIOD})\s*(?:정도|쯤|가량|남짓)?\s*(?:째\s*)?(?:됐|되었|되|지났|지나|흘렀|경과)"),
    re.compile(rf"(?P<p>{_PERIOD})\s*전에\s*(?:예금\s*)?(?:을\s*)?(?:가입|넣|들었|든|예치|개설)"),
]
_TERMINATION = re.compile(r"해지|해약|깨|중도")
_RATE = re.compile(r"금리|이율|이자율|구간")
_NOT_NOW = re.compile(r"다음\s*달|내달|다음\s*주|내년|말에|(?:달|년|월|주|일|개월)\s*(?:후|뒤)|\d+\s*월\s*\d+\s*일|\d{4}-\d{2}-\d{2}")      # 지금 시점이 아닌 해지 질문


def _number(token: str) -> int | None:
    token = token.strip()
    return int(token) if token.isdigit() else _NATIVE.get(token)


def _months_of(span: str) -> int | None:
    span = re.sub(r"\s+", "", span)
    if span.startswith("반년"):
        return 6
    year = re.match(rf"({_NUM})년(반)?", span)
    total = 0
    if year:
        years = _number(year.group(1))
        if years is None:
            return None
        total += years * 12 + (6 if year.group(2) else 0)
        span = span[year.end():]
    month = re.match(rf"({_NUM})(?:개월|달)", span)
    if month:
        months = _number(month.group(1))
        if months is None:
            return None
        total += months
    return total or None


def elapsed_months(question) -> int | None:
    """'가입한 지 N개월 됐다'로 읽히는 경과 개월 수 (읽을 수 없으면 None)."""
    if not isinstance(question, str):
        return None
    for pattern in _ELAPSED:
        m = pattern.search(question)
        if m:
            n = _months_of(m.group("p"))
            return n if n is not None and 1 <= n <= MAX_MONTHS else None
    return None


def lookup_args(question) -> dict | None:
    """코드가 구간 조회 도구를 먼저 부를 질문이면 도구 인자, 아니면 None.
    조건: 경과 기간을 말했고, 중도해지를 말했고, 금리(이율 · 구간)를 묻고, 이자 금액을 묻는 질문이 아니고(계산 도구의 몫), 지금 시점의 질문이다."""
    if not isinstance(question, str) or not (_TERMINATION.search(question) and _RATE.search(question)):
        return None
    if calc_intent(question) or _NOT_NOW.search(question):
        return None
    n = elapsed_months(question)
    return {"elapsed_months": n} if n is not None else None
