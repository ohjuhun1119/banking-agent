"""조항 대조(app/answer_check.ungrounded_terms) 시험: 답변의 퍼센트 · 기간 · 횟수가 근거 글에 있는지. 조항은 직접 쓴 가짜다 (약관 본문은 레포에 없다)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.answer_check import ungrounded_terms  # noqa: E402

CLAUSE = "중도해지 이율은 가입 3개월 이상 6개월 미만 연 0.20%입니다. 일부해지는 2회까지 할 수 있습니다."


def test_a_value_in_the_clause_passes():
    assert ungrounded_terms("3개월 이상이면 연 0.20%예요.", [CLAUSE]) == []


def test_a_percent_not_in_the_clause_is_reported():
    assert ungrounded_terms("연 0.25%예요.", [CLAUSE]) == ["0.25%"]


def test_a_count_not_in_the_clause_is_reported():
    assert ungrounded_terms("일부해지는 3회까지 돼요.", [CLAUSE]) == ["3회"]


def test_a_period_not_in_the_clause_is_reported():
    assert ungrounded_terms("9개월 미만이에요.", [CLAUSE]) == ["9개월"]


def test_the_same_value_in_another_notation_passes():
    assert ungrounded_terms("0.20%", ["연 0.2 %"]) == []
    assert ungrounded_terms("3개월", ["3 개월"]) == []


def test_values_the_user_said_are_allowed():
    assert ungrounded_terms("6개월이면 구간이 달라요.", ["6개월 전에 가입했어요"]) == []


def test_dates_are_not_read_as_periods():
    assert ungrounded_terms("2026년 11월 20일에 해지하면, 11월 20일 기준이에요. 2026-11-20", [CLAUSE]) == []


def test_the_read_values_suffix_added_by_code_is_not_checked():
    assert ungrounded_terms("해지일을 알려 주세요. (질문에서 읽은 값: 가입 12개월)", [CLAUSE]) == []


def test_no_allowed_text_means_any_term_is_reported_once():
    assert ungrounded_terms("0.15%, 0.15%", []) == ["0.15%"]


def test_non_string_answer_and_non_string_allowed_items_are_ignored():
    assert ungrounded_terms(None, [CLAUSE]) == []
    assert ungrounded_terms("0.20%", [None, CLAUSE]) == []


def test_a_number_of_days_the_source_states_without_a_unit_passes():
    """계산 도구의 기본금리 되묻기 결과는 경과일을 숫자로만 가진다 (리뷰 I1). 일수만 단위 없는 숫자를 허용하고, 다른 단위 · 다른 값은 막는다."""
    src = ['{"status": "need_base_rate", "elapsed_days": 279, "contract_days": 365}']
    assert ungrounded_terms("경과 279일이라 기본금리가 필요해요.", src) == []
    assert ungrounded_terms("경과 280일이에요.", src) == ["280일"]
    assert ungrounded_terms("279회 했어요.", src) == ["279회"]
    assert ungrounded_terms("279개월이에요.", src) == ["279개월"]


def test_a_range_written_with_one_unit_covers_both_ends():
    """조항이 `3~6개월`로 쓰면 앞 숫자도 같은 단위다 (리뷰 I3)."""
    src = ["3~6개월: 연 0.2%"]
    assert ungrounded_terms("3개월이면 연 0.2%예요.", src) == []
    assert ungrounded_terms("6개월 미만이에요.", src) == []
    assert ungrounded_terms("4개월이에요.", src) == ["4개월"]
