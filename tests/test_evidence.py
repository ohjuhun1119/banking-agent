"""근거 카드(app/evidence.py) 시험: 도구가 돌려준 것만 보여 주는지, 본문 · 지어낸 값이 들어가지 않는지."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import json  # noqa: E402

import tool_cases  # noqa: E402
from app.evidence import build_evidence, calc_card, calc_intent, format_evidence, source_card  # noqa: E402
from app.tools.interest import calc_interest  # noqa: E402

SRC = {"doc_title": "하나의 정기예금 요약 상품설명서", "section": "중도해지 산출근거", "page": 4, "review_no": "제2025-설명서-199호",
       "effective_date": "2025-09-01", "as_of": "2025-09-01", "secret": "새면 안 되는 값"}


def hit(rank, chunk_id, chunk_type, src=None, text="약관 본문 전체"):
    return {"rank": rank, "chunk_id": chunk_id, "chunk_type": chunk_type, "source": src or SRC, "text": text}


def search_ok(*hits):
    return {"status": "ok", "query": "q", "results": list(hits)}


OK = calc_interest(principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-12-07", base_rate=3.5)
NEED = calc_interest(principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-12-07")


# ---------- 카드 하나 ----------
def test_source_card_keeps_only_source_fields_and_never_the_body():
    c = source_card(hit(1, "x", "중도해지", text="본문"), "검색된 약관")
    assert c == {"kind": "약관", "role": "검색된 약관", "chunk_id": "x", "chunk_type": "중도해지", "doc_title": SRC["doc_title"], "section": "중도해지 산출근거",
                 "page": 4, "review_no": "제2025-설명서-199호", "effective_date": "2025-09-01", "as_of": "2025-09-01"}
    assert "text" not in c and "secret" not in c


def test_calc_card_copies_the_tools_own_sentences():
    c = calc_card(OK)
    assert c["kind"] == "계산" and c["summary"] == OK["summary"] and c["formula"] == OK["formula"] and c["assumptions"] == OK["assumptions"]
    assert "warnings" not in c                                                           # 비어 있으면 넣지 않는다
    assert calc_card(NEED) is None and calc_card({"status": "missing_info"}) is None and calc_card(None) is None


# ---------- 어떤 도구 결과에 어떤 카드가 붙는가 ----------
def test_calculation_answer_gets_the_calc_card_and_the_rule_chunks_source():
    cards = build_evidence([("calc_interest", OK)], rule_lookup=lambda: search_ok(hit(1, "a", "약관_조"), hit(2, "r", "중도해지")))
    assert [c["kind"] for c in cards] == ["계산", "약관"] and cards[1]["chunk_id"] == "r" and "구간표" in cards[1]["role"]


def test_the_rule_chunk_from_this_turns_search_is_used_without_a_second_search():
    called = []
    cards = build_evidence([("search_terms", search_ok(hit(1, "r", "중도해지"))), ("calc_interest", OK)],
                           rule_lookup=lambda: called.append(1) or search_ok())
    assert called == [] and [c["kind"] for c in cards] == ["계산", "약관"] and cards[1]["chunk_id"] == "r"


def test_need_base_rate_gets_only_the_source_that_explains_why_the_rate_is_needed():
    cards = build_evidence([("calc_interest", NEED)], rule_lookup=lambda: search_ok(hit(1, "r", "중도해지")))
    assert [c["kind"] for c in cards] == ["약관"] and "경과 6개월 이상" in cards[0]["role"]


def test_an_unrelated_chunk_is_never_cited_as_the_calculation_basis():
    assert [c["kind"] for c in build_evidence([("calc_interest", OK)], rule_lookup=lambda: search_ok(hit(1, "a", "약관_조")))] == ["계산"]
    assert [c["kind"] for c in build_evidence([("calc_interest", OK)], rule_lookup=lambda: {"status": "not_found", "results": []})] == ["계산"]
    assert [c["kind"] for c in build_evidence([("calc_interest", OK)], rule_lookup=None)] == ["계산"]


def test_a_failing_lookup_does_not_break_the_answer():
    def boom():
        raise RuntimeError("검색 실패")
    assert [c["kind"] for c in build_evidence([("calc_interest", OK)], rule_lookup=boom)] == ["계산"]


@pytest.mark.parametrize("status", ["missing_info", "invalid_input", "not_early_termination", "error"])
def test_no_evidence_when_nothing_was_calculated_or_explained(status):
    assert build_evidence([("calc_interest", {"status": status, "message": "x"})], rule_lookup=lambda: search_ok(hit(1, "r", "중도해지"))) == []


def test_rule_answers_get_the_top_three_searched_sources_without_duplicates():
    r1 = search_ok(*[hit(i, f"c{i}", "약관_조") for i in range(1, 6)])
    r2 = search_ok(hit(1, "c1", "약관_조"), hit(2, "c9", "일부해지"))
    cards = build_evidence([("search_terms", r1), ("search_terms", r2)])
    assert [c["chunk_id"] for c in cards] == ["c1", "c2", "c3"] and all(c["role"] == "검색된 약관" for c in cards)


def test_a_search_with_no_results_says_so_and_no_search_means_no_card():
    cards = build_evidence([("search_terms", {"status": "not_found", "results": []})])
    assert cards == [{"kind": "약관", "found": False, "role": "관련 약관을 찾지 못했습니다"}]
    assert build_evidence([]) == [] and build_evidence([("unknown_tool", {"status": "x"})]) == []


def test_unreadable_tool_results_are_ignored():
    assert build_evidence([("search_terms", None), ("calc_interest", "x"), ("search_terms", {"status": "ok", "results": None})]) == []


# ---------- 글로 만들기 ----------
def test_format_shows_calc_details_and_source_locations():
    text = format_evidence([calc_card(OK), source_card(hit(1, "r", "중도해지"), "중도해지금리 규칙(계산에 쓴 구간표)")])
    assert text.startswith("근거\n- [계산] 2026-01-01 신규, 2026-12-07 해지") and "273,304원" in text
    assert "  · 11개월 이상: 중도해지금리 = 기본금리 3.5%" in text and "  · 가정: 이자 계산의 나누는 수는 365일로 가정했습니다" in text
    assert "- [약관] 하나의 정기예금 요약 상품설명서 · 중도해지 산출근거 · 쪽 4 · 심의 제2025-설명서-199호 · 적용일 2025-09-01 · 기준일 2025-09-01 (중도해지금리 규칙" in text
    assert "새면 안 되는 값" not in text and "약관 본문 전체" not in text


def test_format_edge_cases():
    assert format_evidence([]) == ""
    assert format_evidence([{"kind": "약관", "found": False, "role": "관련 약관을 찾지 못했습니다"}]) == "근거\n- [약관] 관련 약관을 찾지 못했습니다"
    c = source_card(hit(1, "x", "약관_조", {"doc_title": "기본약관", "article": "제9조", "page": 0}), "검색된 약관")
    assert "쪽 0" in format_evidence([c]) and "제9조" in format_evidence([c])
    assert "쪽" not in format_evidence([source_card(hit(1, "x", "약관_조", {"doc_title": "기본약관"}), "검색된 약관")])


# ---------- 계산을 알려 달라는 질문 ----------
@pytest.mark.parametrize("q", [
    "만기 한 달 전인데 지금 해지하는 거랑 만기까지 기다리는 거랑 이자 차이가 많이 나나요?",              # H19
    "예금 중도해지하면 이자 얼마 받아요?", "지금 해지하면 이자 얼마 받을까요?", "이자가 얼마나 나와요?", "해지하면 이자는 얼마예요?",
    "해지 날짜를 오늘로 잡으면 세금 떼고 실제로 얼마 들어오는지 알려줄 수 있어요?", "중도해지하면 손해 이자가 얼마나 되나요", "1000만원 넣으면 이자 얼마 붙어요?",
])
def test_questions_asking_for_an_amount_are_calc_intent(q):
    assert calc_intent(q)


@pytest.mark.parametrize("q", [
    "정기예금 중도해지하면 이율이 어떻게 돼요?", "예금 만기일이 지나도 안 찾았는데 지금 해지하면 이자는 어떻게 계산돼요?",             # H14
    "해지 신청만 해두고 돈은 내일 받아도 오늘 날짜 기준으로 이자 계산되나요?",                                                 # H17
    "가입한 지 8개월 됐는데 지금 해지하면 원금 말고 이자도 받을 수 있나요?",                                               # H7
    "가입할 때 금리를 잘 모르겠는데 해지 이자 확인할 수 있나요?", "1,200만 원 예금 중에 300만 원만 먼저 뺄 수 있어요?", "중도해지하면 처음 약정한 금리는 아예 못 받는 건가요?",
    "이 예금은 예금자보호가 되나요?", "안녕하세요", "", None,
])
def test_questions_about_rules_or_methods_are_not_calc_intent(q):
    assert not calc_intent(q)


def test_no_rule_question_of_the_check_sets_is_sent_to_the_calculator():
    held = json.loads((ROOT / "eval" / "heldout" / "questions.json").read_text(encoding="utf-8"))["questions"]
    for q in held:
        if q["expect"] == "search":
            assert not calc_intent(q["question"]), q["id"]
    for c in tool_cases.CASES:
        if c["group"] == "약관":
            assert not calc_intent(c["question"]), c["id"]


@pytest.mark.parametrize("q", [
    "이자는 얼마를 기준으로 계산돼요?", "이자가 얼마로 계산되는지 방법을 알려주세요", "이자 얼마 받는지 규칙이 뭐예요?", "이자 얼마가 어떻게 나와요?",
    "이자가 얼마 붙는지 산출 방식이 궁금해요", "이자가 얼마로 적용돼요?", "이자가 왜 이만큼 나와요 얼마나 받나요", "이자 얼마 나오는지 계산되는 방식이 뭐예요?",
])
def test_an_amount_word_together_with_a_method_word_is_a_rule_question_not_a_calculation(q):
    assert not calc_intent(q)


def test_the_same_chunk_found_by_two_searches_is_shown_once():
    r1 = search_ok(hit(1, "c1", "약관_조"), hit(2, "c2", "약관_조"))
    r2 = search_ok(hit(1, "c1", "약관_조"), hit(2, "c3", "일부해지"))
    assert [c["chunk_id"] for c in build_evidence([("search_terms", r1), ("search_terms", r2)])] == ["c1", "c2", "c3"]
