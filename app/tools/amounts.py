"""질문 속 금액 읽기 (한국어 단위: 십 · 백 · 천 · 만 · 억).

find_deposit_amount()는 '예금에 넣는 금액'만 찾는다. 금액 바로 뒤에 넣다 · 예금하다 · 가입하다 같은 말이 올 때만 인정해서,
'이자 10만원 받으려면'처럼 다른 뜻의 금액을 가입금액으로 착각하지 않게 한다 (놓치는 경우는 있어도 잘못 잡지는 않는 쪽).
"""
from __future__ import annotations

import re

SMALL_UNITS = {"십": 10, "백": 100, "천": 1_000}
BIG_UNITS = {"만": 10_000, "억": 100_000_000}
_TOKEN = re.compile(r"\d[\d,]*|[십백천만억]")


def parse_korean_amount(expr: str) -> int:
    """'10만' -> 100000, '1,000만' -> 10000000, '천만' -> 10000000, '1억 5천만' -> 150000000, '100,000' -> 100000."""
    total = chunk = current = 0                  # total: 억 · 만 단위까지 확정된 값, chunk: 다음 만/억 앞에서 쌓는 값, current: 방금 읽은 숫자
    tokens = _TOKEN.findall(expr.replace(" ", ""))
    if not tokens:
        raise ValueError(f"금액을 읽을 수 없습니다: {expr!r}")
    for tok in tokens:
        if tok[0].isdigit():
            current = int(tok.replace(",", ""))
        elif tok in SMALL_UNITS:
            chunk += (current or 1) * SMALL_UNITS[tok]
            current = 0
        else:                                    # 만, 억
            chunk += current
            total += (chunk or 1) * BIG_UNITS[tok]
            chunk = current = 0
    return total + chunk + current


_AMOUNT = r"(?<![일이삼사오육칠팔구])(?P<expr>(?:\d[\d,]*|[십백천만억])(?:\s?(?:\d[\d,]*|[십백천만억]))*)\s?원(?!금|리|래|본|칙|장|서|인|천)"
_AFTER = r"\s?(?:정도|쯤|가량)?\s?(?:을|를|으로|로|만|씩|만큼|짜리)?\s?(?:넣|예치|맡|가입|예금|저금|입금|납입)"
_DEPOSIT = re.compile(_AMOUNT + _AFTER)


def find_deposit_amount(text: str) -> int | None:
    """'10만원 넣으면 이자 얼마야?'에서 100000을 찾는다. 예금에 넣는 금액으로 보이는 첫 금액만 돌려주고, 없으면 None."""
    m = _DEPOSIT.search(text or "")
    return parse_korean_amount(m.group("expr")) if m else None
