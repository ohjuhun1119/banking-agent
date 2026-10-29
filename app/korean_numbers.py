"""한글 숫자를 아라비아 숫자로 바꾼다 (값 근거 대조에서 '일천만원', '삼점오 퍼센트', '일 년 만기'를 읽기 위해).

읽기 전용이다: 사용자가 한 말에서 값을 찾을 때만 쓰고, 화면에 보이는 글은 바꾸지 않는다.
  금액   '원' 앞의 한글 숫자 (이천만원 -> 20000000원, 천 오백만 원 -> 15000000 원). 단위(십 · 백 · 천 · 만 · 억)가 하나는 있어야 한다
  금리   '삼점오 퍼센트' -> '3.5 퍼센트', '삼 프로' -> '3 프로'
  기간   '일 년 만기' -> '1 년 만기'
한계: 공백 없이 앞 글자에 붙은 숫자('예금이천만원')와 '이십오점삼' 같은 긴 소수는 읽지 않는다 (틀린 값을 쓰느니 되묻는다).
"""
from __future__ import annotations

import re

_DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_SMALL = {"십": 10, "백": 100, "천": 1000}
_BIG = {"만": 10_000, "억": 100_000_000}
_CHARS = "영공일이삼사오육칠팔구십백천만억"
_UNITS = set(_SMALL) | set(_BIG)

_AMOUNT = re.compile(rf"(?<![가-힣\d.,])([{_CHARS}]+(?:\s[{_CHARS}]+)*)(?=\s?원)")        # 숫자 바로 뒤의 단위(1000만원, 5천만원)는 건드리지 않는다
_RATE_DEC = re.compile(rf"(?<![가-힣])([영공일이삼사오육칠팔구십]+)\s?점\s?([영공일이삼사오육칠팔구]+)(?=\s?(?:퍼센트|프로|%))")
_RATE_INT = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구십]+)(?=\s?(?:퍼센트|프로))")
_TERM = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구십]+)\s?(년|개월|달)(?=\s?(?:짜리|만기|제))")


def hangul_to_int(text: str) -> int:
    """'일천만' -> 10000000, '천 오백만' -> 15000000, '만' -> 10000. 읽을 수 없는 글자가 있으면 ValueError."""
    total = section = num = 0
    for ch in text:
        if ch.isspace():
            continue
        if ch in _DIGITS:
            num = _DIGITS[ch]
        elif ch in _SMALL:
            section += (num or 1) * _SMALL[ch]
            num = 0
        elif ch in _BIG:
            section += num
            total += (section or 1) * _BIG[ch]
            section = num = 0
        else:
            raise ValueError(f"한글 숫자가 아닙니다: {ch!r}")
    return total + section + num


def normalize_numerals(text: str) -> str:
    """금액 · 금리 · 기간에 쓴 한글 숫자를 아라비아 숫자로 바꾼 글을 돌려준다. 바꿀 것이 없으면 그대로."""
    if not isinstance(text, str):
        return text

    def amount(m):
        s = m.group(1)
        if not any(c in _UNITS for c in s):                  # '구원' 같은 일반 낱말은 건드리지 않는다
            return m.group(0)
        try:
            return str(hangul_to_int(s))
        except ValueError:
            return m.group(0)

    def rate_dec(m):
        try:
            return f"{hangul_to_int(m.group(1))}.{''.join(str(_DIGITS[c]) for c in m.group(2))}"
        except ValueError:
            return m.group(0)

    def rate_int(m):
        try:
            return str(hangul_to_int(m.group(1)))
        except ValueError:
            return m.group(0)

    def term(m):
        try:
            return f"{hangul_to_int(m.group(1))}{m.group(2)}"
        except ValueError:
            return m.group(0)

    text = _AMOUNT.sub(amount, text)
    text = _RATE_DEC.sub(rate_dec, text)
    text = _RATE_INT.sub(rate_int, text)
    return _TERM.sub(term, text)
