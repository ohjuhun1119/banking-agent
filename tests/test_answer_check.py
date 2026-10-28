"""답변 점검(app/answer_check.py) 시험: 실제 대화에서 나온 오류(중국어 섞임, 지어낸 전화번호, 상담원 연결 요청)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.answer_check import foreign_script, handoff_request, phone_numbers, unsupported_phone_numbers  # noqa: E402

# 실제 대화(스크린샷)에서 모델이 한 답변
REAL_CHINESE = "잠시 기다려 주세요. 상담원이 연결될 때까지 기다려 주십시오. 또는 고객센터 번호 1544-1111을 직접拨打以转接人工客服。"
REAL_FABRICATED = "상담원 연결을 요청해주시면 됩니다. 잠시 후 다시 시도하거나, 고객센터 번호 1544-1111을 통해 상담원과 연결하실 수 있습니다."


# ---------- 한국어가 아닌 글자 ----------
@pytest.mark.parametrize("text", [REAL_CHINESE, "이자는 利息입니다", "中途解约利率", "こんにちは", "カタカナ", "한국어 사이에 汉字"])
def test_chinese_and_japanese_are_detected(text):
    assert foreign_script(text)


@pytest.mark.parametrize("text", [
    "세전 이자는 273,304원입니다.", "『하나의 정기예금』 상품설명서 기준입니다.", "「중도해지」 구간 · ※ 참고 — 2.934%", "Hana Bank 1599-1111",
    "연 3.5% × 90% × (340일/365일)", "", "한글만 있는 답변입니다. 마침표와 쉼표, 물음표?", "①②③ → ⇄ ~ ∼",
])
def test_korean_and_ordinary_symbols_are_not_flagged(text):
    assert not foreign_script(text)


def test_foreign_script_ignores_non_text():
    assert foreign_script(None) is False and foreign_script(123) is False


# ---------- 전화번호 ----------
@pytest.mark.parametrize("text,found", [
    ("고객센터 번호 1544-1111을 통해", ["1544-1111"]), ("대표번호 1599-1111, 1588-1111", ["1599-1111", "1588-1111"]),
    ("02-123-4567로", ["02-123-4567"]), ("010-1234-5678", ["010-1234-5678"]), ("080-123-4567", ["080-123-4567"]),
    ("전화 02-1234-5678", ["02-1234-5678"]),
])
def test_phone_numbers_are_found(text, found):
    assert phone_numbers(text) == found


@pytest.mark.parametrize("text", [
    "2026-12-07에 해지", "신규일 2026-01-01, 만기일 2027-01-01", "2026-01-01~2027-01-01", "273,304원", "연 2.934%", "340일", "제9조의2 ⑤",
    "1000만원", "10,000,000원", "2026-12", "12-07",
    "카드번호 1234-5678-9012-3456", "계좌번호 110-123-456789", "버전 10-123-45678",                     # 카드 · 계좌번호 모양을 전화번호로 착각하지 않는다
])
def test_dates_amounts_and_rates_are_not_phone_numbers(text):
    assert phone_numbers(text) == []


def test_the_fabricated_number_from_the_real_conversation_is_unsupported():
    assert unsupported_phone_numbers(REAL_FABRICATED) == ["1544-1111"]
    assert unsupported_phone_numbers(REAL_CHINESE, "사용자: 상담원 연결해줘") == ["1544-1111"]


def test_numbers_in_tool_results_or_user_words_are_allowed():
    assert unsupported_phone_numbers("고객센터 1599-1111로 문의하세요", '{"text": "고객센터(1599-1111)로 연락"}') == []
    assert unsupported_phone_numbers("1588-1111", "제 번호는 1588-1111이에요") == []
    assert unsupported_phone_numbers("1599-1111과 1544-1111", "고객센터 1599-1111") == ["1544-1111"]
    assert unsupported_phone_numbers("번호 없음", "1599-1111") == []
    assert unsupported_phone_numbers("1544-1111", None, 5) == ["1544-1111"]


# ---------- 상담원 연결 요청 ----------
@pytest.mark.parametrize("text", [
    "상담원 연결해줘", "상담원 연결 요청", "상담원 연결해주세요", "상담사랑 통화하고 싶어요", "사람이랑 통화하고 싶어요", "직원 연결해 주세요",
    "담당자와 상담하고 싶어요", "고객센터 연결해줘", "고객센터에 전화 연결", "전화 연결 부탁해요", "콜센터 번호 알려줘", "상담 직원 불러줘",
    "저 상담원이랑 얘기할래요",
])
def test_handoff_requests_are_detected(text):
    assert handoff_request(text)


@pytest.mark.parametrize("text", [
    "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요?", "중도해지하면 이율이 어떻게 돼요?", "3.5%",
    "상담 시간이 어떻게 돼요?", "안녕하세요", "예금자보호가 되나요?", "고객님께 안내드립니다", "", "직원 수가 몇 명이에요?",
])
def test_ordinary_questions_are_not_handoff_requests(text):
    assert not handoff_request(text)


def test_handoff_request_ignores_non_text():
    assert handoff_request(None) is False
