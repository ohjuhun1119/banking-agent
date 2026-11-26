"""API 모드에서 모델로 보내기 전에 개인정보 모양을 가리는 함수 시험. 지워야 할 것과 지우면 안 되는 것(금액 · 날짜 · 금리)을 함께 고정한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.redact import mask, mask_messages  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402


@pytest.mark.parametrize("text,tag", [("제 번호는 010-1234-5678이에요", "[전화번호]"), ("연락처 01012345678 입니다", "[전화번호]"), ("02-123-4567로 전화 주세요", "[전화번호]"),
                                      ("주민번호 900101-1234567입니다", "[주민번호]"), ("카드 1234-5678-9012-3456 분실", "[카드번호]"),
                                      ("메일은 hong.gildong+bank@example.co.kr 입니다", "[이메일]"), ("계좌 110-123-456789 에서 이체", "[계좌번호]")])
def test_personal_number_shapes_are_masked(text, tag):
    out = mask(text)
    assert tag in out and "@example" not in out
    assert "5678" not in out and "1234567" not in out and "123-456789" not in out and "hong" not in out


@pytest.mark.parametrize("text", ["1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요", "원금 10000000원, 해지일 2026-12-07", "기본금리는 연 3.5%예요", "중도해지금리 2.934%",
                                  "6개월 이상 9개월 미만 구간", "100만 원 이상 가입", "1,000,000원", "제9조 ⑤항 근거", "계약기간 365일, 경과 279일", "고객센터 1588-1111"])
def test_amounts_dates_rates_and_clauses_are_left_alone(text):
    assert mask(text) == text


def test_masking_twice_changes_nothing_more():
    once = mask("전화 010-1234-5678, 메일 a@b.com")
    assert mask(once) == once


def test_a_phone_number_next_to_an_amount_masks_only_the_phone():
    out = mask("010-1234-5678이고 1000만원을 2026-01-01에 넣었어요")
    assert "[전화번호]" in out and "1000만원" in out and "2026-01-01" in out


def test_mask_messages_covers_every_role_and_block_content_without_touching_the_originals():
    original = [SystemMessage("규칙"), HumanMessage("번호 010-1234-5678"), AIMessage(content=[{"type": "text", "text": "010-1234-5678로 연락"}, {"type": "tool_use", "id": "x"}]),
                HumanMessage("메일 a@b.com")]
    out = mask_messages(original)
    assert out[1].content == "번호 [전화번호]" and out[3].content == "메일 [이메일]"
    assert out[2].content[0]["text"] == "[전화번호]로 연락" and out[2].content[1] == {"type": "tool_use", "id": "x"}
    assert original[1].content == "번호 010-1234-5678" and original[3].content == "메일 a@b.com"          # 상태에 저장된 원본은 그대로


def test_tool_call_arguments_are_masked_too_because_the_forced_search_query_is_the_users_own_question():
    """API 모드 끝까지 시험이 잡은 누출: 코드가 부른 검색의 query 인자에 사용자 질문 원문(전화번호 포함)이 들어간다."""
    call = {"name": "search_terms", "args": {"query": "010-1234-5678 중도해지 이자", "k": 5, "filters": {"note": "a@b.com"}}, "id": "c1", "type": "tool_call"}
    raw = {"id": "c1", "type": "function", "function": {"name": "search_terms", "arguments": '{"query": "010-1234-5678 중도해지 이자"}'}}
    original = [AIMessage(content="", tool_calls=[call], additional_kwargs={"tool_calls": [raw]})]
    out = mask_messages(original)[0]
    assert out.tool_calls[0]["args"] == {"query": "[전화번호] 중도해지 이자", "k": 5, "filters": {"note": "[이메일]"}}
    assert "5678" not in out.additional_kwargs["tool_calls"][0]["function"]["arguments"] and out.tool_calls[0]["name"] == "search_terms"
    assert original[0].tool_calls[0]["args"]["query"].startswith("010-1234-5678")                  # 원본은 그대로
