"""질문에서 읽은 값으로 빠뜨린 칸 채우기 시험 (오늘 = 2026-10-28). 핵심: 틀린 값을 채우느니 비워 둔다."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import tool_cases  # noqa: E402
from app.slots import fill_missing_from_text, message_slots, slots_from_texts  # noqa: E402

TODAY = date(2026, 10, 28)


def slots(text):
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in message_slots(text, TODAY).items()}


def test_all_four_values_of_a_complete_question_are_read():
    q = "정기예금 1,000만 원을 2025년 6월 1일에 들었고 2026년 6월 1일 만기인데, 오늘 해지하면 이자 얼마예요?"
    assert slots(q) == {"start_date": "2025-06-01", "maturity_date": "2026-06-01", "termination_date": "2026-10-28", "principal": 10_000_000}


def test_the_maturity_date_the_model_forgot_is_filled_but_the_models_own_values_are_kept():
    q = "2025년 9월 15일에 가입해서 2026년 9월 15일 만기인 700만 원 예금, 7월 말에 해지하면 이자 좀 계산해 주세요."
    args, filled = fill_missing_from_text({"principal": 7_000_000, "start_date": "2025-09-15"}, [q], TODAY)
    assert filled == {"maturity_date": "2026-09-15"} and args["principal"] == 7_000_000 and args["maturity_date"] == "2026-09-15"
    args, filled = fill_missing_from_text({"start_date": "2024-01-01"}, ["2025-06-01에 가입했어요"], TODAY)       # 모델이 넣은 값은 덮어쓰지 않는다
    assert args["start_date"] == "2024-01-01" and "start_date" not in filled


def test_blank_values_count_as_missing():
    args, filled = fill_missing_from_text({"principal": None, "start_date": "  "}, ["1000만원을 2026-01-01에 가입했어요"], TODAY)
    assert args["principal"] == 10_000_000 and args["start_date"] == "2026-01-01" and set(filled) == {"principal", "start_date"}


@pytest.mark.parametrize("text,expected", [
    ("만기는 내년 4월 2일이에요, 10월 15일에 해지하면 얼마 받아요?", {"maturity_date": "2027-04-02", "termination_date": "2026-10-15"}),
    ("다음 달 20일에 깨면 이자 얼마예요?", {"termination_date": "2026-11-20"}),
    ("2026년 1월 1일에 가입해서 1년 만기로 넣었는데 2026년 3월 2일에 해지하면",
     {"start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-03-02"}),                 # '1년 만기'로 만기일을 계산
    ("10만원을 2026-01-01부터 2027-01-01까지 넣었다가 2026-05-31에 해지하면", {"start_date": "2026-01-01", "termination_date": "2026-05-31"}),   # '까지'는 모른다
    ("2026-01-01에 가입했고 6개월짜리예요", {"start_date": "2026-01-01", "maturity_date": "2026-07-01"}),            # 기간으로 만기일을 계산
    ("기본금리는 3.5%였어요", {"base_rate": 3.5}), ("기본금리는 삼점오 퍼센트였어요", {"base_rate": 3.5}),
    ("원금 1000만원이에요", {"principal": 10_000_000}), ("일천만원 넣었어요", {"principal": 10_000_000}),
])
def test_clear_values_are_read(text, expected):
    assert slots(text) == expected


@pytest.mark.parametrize("text", [
    "1,200만 원 예금 중에 300만 원만 먼저 뺄 수 있어요?",                         # 금액이 둘: 어느 것이 원금인지 모른다
    "금리 연 3%로 500만 원을 1년 예치했는데 6개월 만에 깨면",                      # '금리 연 3%'는 기본금리라고 말한 것이 아니다 (원금만 채운다)
    "7월 말에 해지하면 이자 얼마예요?", "5개월 전에 가입했어요", "2026-01-01이에요",     # 대략적이거나 역할을 모르는 날짜
    "2년 만기(2026-01-01 ~ 2028-01-01)로 넣었어요",
    "12월 7일에 가입했어요",                                                       # 오늘(10월 28일)보다 뒤의 날짜를 가입일이라고 읽지 않는다
    "안녕하세요", "", "중도해지하면 이율이 어떻게 돼요?",
])
def test_uncertain_things_are_left_empty(text):
    s = slots(text)
    assert "base_rate" not in s and "maturity_date" not in s and "start_date" not in s
    if "1,200만" in text or "300만" in text:
        assert "principal" not in s


def test_two_dates_with_the_same_role_are_not_filled():
    assert "termination_date" not in slots("2026-10-01에 해지하려다가 2026-11-01에 해지하기로 했어요")


def test_later_messages_override_earlier_ones_and_missing_ones_are_remembered():
    texts = ["1000만원을 2026-01-01에 가입했어요. 2026-12-07에 해지하면?", "아 해지는 2026-11-01에 할게요"]
    s = slots_from_texts(texts, TODAY)
    assert s["principal"] == 10_000_000 and s["start_date"] == date(2026, 1, 1) and s["termination_date"] == date(2026, 11, 1)


def test_non_text_messages_are_ignored():
    assert slots_from_texts([None, 3, "1000만원을 2026-01-01에 가입했어요"], TODAY)["principal"] == 10_000_000


# ---------- 확인용 질문(개발용)과 검증용 gold: 채운 값이 틀리면 안 된다 ----------
CALC_CASES = [c for c in tool_cases.CASES if c["expect"]["tool"] == "calc_interest"]


@pytest.mark.parametrize("case", CALC_CASES, ids=[c["id"] for c in CALC_CASES])
def test_values_filled_from_the_check_questions_match_the_expected_arguments(case):
    e = case["expect"]
    gold = e["args"] if e["strict"] else e["allowed"]
    _, filled = fill_missing_from_text({}, [case["question"]], date.fromisoformat(tool_cases.TODAY))
    for fld, value in filled.items():
        assert fld in gold, (fld, value)
        assert float(value) == float(gold[fld]) if fld in ("base_rate", "principal") else str(value) == str(gold[fld])


HELD = json.loads((ROOT / "eval" / "heldout" / "questions.json").read_text(encoding="utf-8"))["questions"]
# 측정 전에 고정한 gold에는 없지만 질문에 분명히 적힌 값 (라벨을 고치지 않고 예외로 둔다)
GOLD_OMISSIONS = {("H14", "termination_date")}


@pytest.mark.parametrize("q", HELD, ids=[q["id"] for q in HELD])
def test_values_filled_for_the_heldout_questions_never_contradict_the_frozen_gold(q):
    gold = {k: v["value"] for k, v in q["gold"].items()}
    _, filled = fill_missing_from_text({}, [q["question"]], date(2026, 10, 28))
    for fld, value in filled.items():
        if (q["id"], fld) in GOLD_OMISSIONS:
            assert value == "2026-10-28"
            continue
        assert fld in gold, (q["id"], fld, value)
        assert float(value) == float(gold[fld]) if fld in ("principal", "base_rate") else value == gold[fld]


def test_the_maturity_dates_the_real_model_forgot_are_now_filled():
    """검증용 실제 모델 결과: H1 · H6 · H13에서 모델이 질문에 적힌 만기일을 넣지 않았다."""
    want = {"H1": "2026-06-01", "H6": "2026-09-15", "H13": "2027-04-02"}
    for q in HELD:
        if q["id"] in want:
            assert fill_missing_from_text({}, [q["question"]], date(2026, 10, 28))[1]["maturity_date"] == want[q["id"]]
