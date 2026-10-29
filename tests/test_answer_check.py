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


# ---------- 답변 본문의 날짜 · 금액 · 도구 문법 · '약관에서 확인되지 않습니다' ----------
from datetime import date  # noqa: E402

from app.answer_check import not_found_claim, tool_syntax, ungrounded_values  # noqa: E402

TODAY = date(2026, 10, 28)
# 검증용 20문항 실제 모델 결과(수정 전)의 답변 (출력에 잘린 부분까지)
REAL = {
    "H3": ("정기예금 2천 넣은 지 5개월쯤 됐어요. 지금 해지하면 이자 얼마 받을까요?",
           "계산에 필요한 정보가 부족합니다. 원금과 신규일, 만기일을 알려주시면 정확한 이자 금액을 알려드릴 수 있습니다. 알려주신 원금은 2000만원, 신규일은2026-05-28입니다.",
           ["2026-05-28", "20,000,000원"]),
    "H4": ("작년 12월에 가입한 예금 오늘 해지하려고 하는데 이자가 얼마나 될까요?",
           "계산을 위해서는 원금, 신규일, 만기일이 필요합니다. 원금은 가입 시입력하신 금액이고, 신규일은 2025-12-01이었습니다. 만기일은 오늘인 2026-10-28입니다.",
           ["2025-12-01"]),                                                    # 2026-10-28은 오늘이라 허용 (만기일이라고 한 것은 이 점검이 못 잡는다)
    "H5": ("금리 연 3.2%로 1,500만 원 넣었어요. 만기까지 두 달 남았는데 지금 깨면 이자 얼마나 손해예요?",
           "계산을 위해서는 신규일과 만기일이 필요합니다. 신규일은 언제인가요?만기일은 2026년 12월 28일입니다.", ["2026-12-28"]),
    "H16": ("500만 원을 연 3%로 1년 예치했는데 6개월 만에 깨면 이자 얼마나 붙어요?",
            "계산에 필요한 값이 부족합니다: 신규일, 만기일, 해지일을 알려주세요. 사용자가 말하지 않은 값(신규일 2026-10-28, 만기일 2027-10-28, 해지일 2026-04-28)은 계산에 사용되",
            ["2026-04-28", "2027-10-28"]),
    "H20": ("해지 날짜를 오늘로 잡으면 세금 떼고 실제로 얼마 들어오는지 알려줄 수 있어요?",
            "계산을 위해서는 원금, 신규일, 만기일이 필요합니다. 원금은 예를 들어 1000만원이라고 가정했고, 신규일은 2023년 1월 1일로 잡았습니다.",
            ["2023-01-01", "10,000,000원"]),
}


@pytest.mark.parametrize("qid", list(REAL))
def test_values_the_real_model_invented_in_its_answers_are_found(qid):
    question, answer, expected = REAL[qid]
    assert sorted(ungrounded_values(answer, [question], TODAY)) == sorted(expected)


def test_a_good_real_answer_with_tool_results_passes():
    q = "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요?"
    tool = '{"status": "ok", "interest_krw": 273304, "principal": 10000000, "termination_date": "2026-12-07", "start_date": "2026-01-01"}'
    a = ("해지일 2026-12-07에 1000만원을 해지하면, 경과 340일(11개월) 동안의 중도해지금리 연 2.934%에 따라 세전 이자는 273,304원입니다. "
         "이자 = 원금 10,000,000원 × 2.934% × 340일 / 365일 = 273,304원 (원 미만 절사)")
    assert ungrounded_values(a, [q, tool], TODAY) == []
    assert ungrounded_values(a, [q], TODAY) == ["273,304원"]                      # 도구 결과가 없으면 계산한 이자 금액은 근거가 없다


@pytest.mark.parametrize("answer,allowed,expected", [
    ("오늘은 2026-10-28이에요", [], []), ("신규일은 2026-01-01이에요", ["2026-01-01에 가입"], []),
    ("신규일은 2026-01-01이에요", ["1월 1일에 가입"], []),                       # 연도 없이 말한 날짜: 올해면 허용
    ("신규일은 2025-01-01이에요", ["1월 1일에 가입"], ["2025-01-01"]),            # 다른 해는 허용하지 않는다
    ("12월 7일에 해지하신다고 하셨어요", ["2026-12-07에 해지"], []), ("12월 8일에 해지하신다고 하셨어요", ["2026-12-07에 해지"], ["12월 8일"]),
    ("만기일은 2027-10-28입니다", ["오늘 해지하면 얼마예요"], ["2027-10-28"]),    # 월 · 일이 오늘과 같아도 연도가 다르면 허용하지 않는다
    ("원금 천만 원이시군요", ["1000만원 넣었어요"], []), ("원금 2천만 원이시군요", ["1000만원 넣었어요"], ["20,000,000원"]),
    ("이자는 273,304원이에요", ['{"interest_krw": 273304}'], []), ("이자는 273,305원이에요", ['{"interest_krw": 273304}'], ["273,305원"]),
    ("만기 전에 해지하면 340일 동안 연 2.934%가 적용돼요", [], []),                # 작은 숫자 · 금리 · '만기'의 '만'은 점검하지 않는다
    ("최소 가입금액은 100만원이에요", ['{"text": "최소 가입금액은 100만원"}'], []), ("만원 단위로", [], []), ("", [], []),
])
def test_ungrounded_values(answer, allowed, expected):
    assert ungrounded_values(answer, allowed, TODAY) == expected


def test_ungrounded_values_ignores_non_text():
    assert ungrounded_values(None, ["x"], TODAY) == [] and ungrounded_values("2026-01-01", [None, 3], TODAY) == ["2026-01-01"]


@pytest.mark.parametrize("text", [
    '(calc_interest {"termination_date": "2026-10-28", "principal": 10000000})', "search_terms(query=중도해지)", '{"query": "중도해지"}',
    "<tool_call>", "calc_interest를 호출합니다", "{'name': 'calc_interest'}",
])
def test_tool_syntax_in_the_answer_is_detected(text):
    assert tool_syntax(text)


@pytest.mark.parametrize("text", ["세전 이자는 273,304원입니다.", "중도해지 이율은 약관에 따라 달라요 (예: 연 0.10%)", "{중괄호}만 있어요", "", "이자 계산 도구로 계산했어요"])
def test_ordinary_answers_are_not_tool_syntax(text):
    assert not tool_syntax(text)


@pytest.mark.parametrize("text", ["약관에서 확인되지 않습니다.", "약관에서 확인되지 않습니다. 일부해지와 관련된 내용을 알려주시면", "약관에는 확인할 수 없습니다", "약관에서 확인할 수 없어요", "약관에 확인되지 않아요"])
def test_not_found_claim_is_detected(text):
    assert not_found_claim(text)


@pytest.mark.parametrize("text", ["약관에 따르면 일부 해지가 가능합니다", "확인되지 않았어요", "약관 내용을 확인했어요", "", None])
def test_other_statements_are_not_not_found_claims(text):
    assert not not_found_claim(text)
