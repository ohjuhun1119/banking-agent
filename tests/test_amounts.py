"""질문 속 금액 읽기(app/tools/amounts.py) 시험. 가입금액으로 보이는 금액만 잡고, 다른 뜻의 금액은 잡지 않는지 확인한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools.amounts import find_deposit_amount, parse_korean_amount  # noqa: E402


@pytest.mark.parametrize("expr,expected", [
    ("100000", 100_000), ("100,000", 100_000), ("10만", 100_000), ("10 만", 100_000), ("1,000만", 10_000_000), ("천만", 10_000_000),
    ("백만", 1_000_000), ("십만", 100_000), ("만", 10_000), ("5천", 5_000), ("천", 1_000), ("1억", 100_000_000), ("1억 5천만", 150_000_000),
    ("3억2천만", 320_000_000), ("10만5천", 105_000), ("2천5백", 2_500), ("50만", 500_000),
])
def test_parse_korean_amount(expr, expected):
    assert parse_korean_amount(expr) == expected


def test_parse_korean_amount_rejects_text_without_a_number():
    with pytest.raises(ValueError):
        parse_korean_amount("얼마")


@pytest.mark.parametrize("question,expected", [
    ("10만원 넣으면 이자 얼마야?", 100_000),
    ("10만 원 예금하면 이자가 얼마예요?", 100_000),
    ("10만원을 넣으면 이자가 얼마나 붙어요?", 100_000),
    ("100,000원으로 가입하면 이자는요?", 100_000),
    ("5천원 가입하면?", 5_000),
    ("십만원 넣으면 이자 얼마야", 100_000),
    ("10만원 정도 넣으면 이자는?", 100_000),
    ("10만원 맡기면 얼마 받아요?", 100_000),
    ("50만원짜리 예금에 가입하면?", 500_000),
    ("100만원 넣으면 이자 얼마야?", 1_000_000),
    ("1000만원을 예금하면 중도해지 이자는?", 10_000_000),
    ("1억 5천만원 넣으면?", 150_000_000),
])
def test_finds_the_amount_being_deposited(question, expected):
    assert find_deposit_amount(question) == expected


@pytest.mark.parametrize("question", [
    "이자 10만원 받으려면 얼마 넣어야 해요?",          # 이자로 받는 금액
    "10만원 이자 받으려면 얼마를 넣어야 하나요?",       # 금액 뒤에 바로 넣다가 오지 않음
    "원금 100만 원 이상이어야 하나요?",                 # '원금'의 '원'을 금액 단위로 착각하지 않기
    "10만원 이자는요?",                                  # 넣는다는 말이 없으면 가입금액인지 알 수 없음
    "중도해지하면 이자 얼마 받아요?",
    "최소 가입금액이 얼마예요?",
    "이만원 넣으면 이자 얼마야?",                        # 한글 숫자(이, 삼, 오...)는 읽지 않는다 (틀린 금액을 말하느니 놓친다)
    "",
])
def test_ignores_amounts_that_are_not_the_deposit(question):
    assert find_deposit_amount(question) is None


def test_none_input_is_safe():
    assert find_deposit_amount(None) is None
