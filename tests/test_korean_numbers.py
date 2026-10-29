"""한글 숫자 읽기 시험."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.korean_numbers import hangul_to_int, normalize_numerals  # noqa: E402


@pytest.mark.parametrize("text,value", [
    ("일천만", 10_000_000), ("이천만", 20_000_000), ("천 오백만", 15_000_000), ("천오백만", 15_000_000), ("오백만", 5_000_000),
    ("만", 10_000), ("천", 1000), ("백만", 1_000_000), ("삼억", 300_000_000), ("일억오천만", 150_000_000), ("오천만", 50_000_000),
    ("십", 10), ("이십", 20), ("백오십", 150), ("삼천이백", 3200), ("영", 0), ("구", 9), ("이만오천", 25_000),
])
def test_hangul_to_int(text, value):
    assert hangul_to_int(text) == value


def test_hangul_to_int_rejects_other_characters():
    with pytest.raises(ValueError):
        hangul_to_int("이천x")


@pytest.mark.parametrize("text,expected", [
    ("일천만원을 예금했어요", "10000000원을 예금했어요"), ("이천만원 가입했어요", "20000000원 가입했어요"),
    ("천 오백만 원 넣었어요", "15000000 원 넣었어요"), ("예금 오백만원이에요", "예금 5000000원이에요"), ("삼억 원 넣었어요", "300000000 원 넣었어요"),
    ("만원 넣었어요", "10000원 넣었어요"), ("천원이에요", "1000원이에요"),
    ("연 삼점오 퍼센트였어요", "연 3.5 퍼센트였어요"), ("삼점오프로예요", "3.5프로예요"), ("삼 프로였어요", "3 프로였어요"), ("영점오 퍼센트", "0.5 퍼센트"),
    ("일 년 만기로 가입", "1년 만기로 가입"), ("육 개월짜리예요", "6개월짜리예요"), ("삼 년 만기", "3년 만기"),
])
def test_normalize_numerals(text, expected):
    assert normalize_numerals(text) == expected


@pytest.mark.parametrize("text", [
    "구원받았어요", "이원화 시스템", "일 원", "만기가 다가와요", "1000만원 넣었어요", "1,000만 원 넣었어요", "5천만원이에요", "1.5억 넣었어요", "2천만 원", "3.5% 였어요", "2026-01-01에 가입", "예금이천만원",       # 낱말 · 이미 숫자 · 붙여 쓴 것은 그대로
    "해지하면 얼마예요?", "", "삼성전자",
])
def test_normalize_leaves_ordinary_text_alone(text):
    assert normalize_numerals(text) == text


def test_normalize_handles_non_text():
    assert normalize_numerals(None) is None and normalize_numerals(5) == 5
