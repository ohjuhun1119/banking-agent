"""날짜 표현 찾기 · 상대 날짜 계산 · 역할 읽기 시험 (오늘 = 2026-10-28)."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relative_dates import date_mentions, find_mentions, mask_mentions, resolve_word  # noqa: E402

TODAY = date(2026, 10, 28)


def dates(text):
    return [m.date for m in find_mentions(text, TODAY)]


@pytest.mark.parametrize("text,expected", [
    ("2026-01-01에 넣었어요", [date(2026, 1, 1)]), ("2026.1.1", [date(2026, 1, 1)]), ("2026/01/01", [date(2026, 1, 1)]),
    ("2026년 1월 1일에", [date(2026, 1, 1)]), ("2026년1월1일", [date(2026, 1, 1)]), ("20260101에 가입", [date(2026, 1, 1)]),
    ("12월 7일에 해지", [date(2026, 12, 7)]), ("1/1에 가입", [date(2026, 1, 1)]), ("올해 1월 1일", [date(2026, 1, 1)]),
    ("작년 12월 7일에 가입", [date(2025, 12, 7)]), ("내년 4월 2일 만기", [date(2027, 4, 2)]),
    ("다음 달 20일에 깨면", [date(2026, 11, 20)]), ("이번 달 30일에", [date(2026, 10, 30)]), ("지난달 5일에 가입", [date(2026, 9, 5)]),
    ("어제 해지했어요", [date(2026, 10, 27)]), ("내일 해지", [date(2026, 10, 29)]), ("모레", [date(2026, 10, 30)]), ("그저께", [date(2026, 10, 26)]),
    ("오늘 해지", [TODAY]), ("지금 깨면", [TODAY]), ("당장", [TODAY]),
    ("2025년 6월 1일에 들었고 2026년 6월 1일 만기인데", [date(2025, 6, 1), date(2026, 6, 1)]),
])
def test_dates_are_found_and_relative_ones_are_computed_from_today(text, expected):
    assert dates(text) == expected


def test_month_rollover_in_relative_months():
    assert [m.date for m in find_mentions("다음 달 20일", date(2026, 12, 15))] == [date(2027, 1, 20)]
    assert [m.date for m in find_mentions("지난달 25일", date(2026, 1, 10))] == [date(2025, 12, 25)]


@pytest.mark.parametrize("text", [
    "지금까지 5개월 됐어요", "2026-02-30에 가입", "13월 40일", "340일/365일", "7월 말에 해지", "3개월 전에 가입", "다음 주에", "5일 후에", "10만원 넣었어요",
    "2026-12", "1000만원", "금액 20260101원", "", "안녕하세요", "다음 달 32일", "3.5%",
])
def test_things_that_are_not_exact_dates_are_not_found(text):
    assert dates(text) == []


def test_non_text_is_ignored():
    assert find_mentions(None, TODAY) == [] and date_mentions(None, TODAY) == []


def test_mentions_have_positions_and_kinds_and_are_in_order():
    ms = find_mentions("12월 7일에 해지, 2026-01-01에 가입, 어제", TODAY)
    assert [m.kind for m in ms] == ["monthday", "full", "relative"] and [m.start for m in ms] == sorted(m.start for m in ms)
    assert ms[0].month_day == (12, 7) and ms[1].month_day is None


def test_a_date_is_not_found_twice():
    assert len(find_mentions("2026년 1월 1일", TODAY)) == 1                      # '1월 1일'을 따로 또 읽지 않는다
    assert len(find_mentions("내년 4월 2일", TODAY)) == 1
    assert len(find_mentions("작년 12월 7일에 가입, 12월 7일에 해지", TODAY)) == 2


def test_mask_mentions_keeps_positions():
    text = "2026-01-01에 1000만원"
    masked = mask_mentions(text, find_mentions(text, TODAY))
    assert len(masked) == len(text) and "2026" not in masked and "1000만원" in masked


@pytest.mark.parametrize("word,expected", [("오늘", TODAY), ("어제", date(2026, 10, 27)), ("내일", date(2026, 10, 29)), ("지금", TODAY), (" 오늘 ", TODAY)])
def test_resolve_word(word, expected):
    assert resolve_word(word, TODAY) == expected


def test_resolve_word_unknown():
    assert resolve_word("다음주", TODAY) is None and resolve_word("", TODAY) is None


# ---------- 역할: 날짜 바로 앞의 라벨이나 바로 뒤에 붙은 키워드만 믿는다 (멀리 있는 키워드로는 추측하지 않는다) ----------
def roles(text):
    return [(m.date.isoformat(), m.role) for m in date_mentions(text, TODAY)]


@pytest.mark.parametrize("text,expected", [
    ("정기예금 1,000만 원을 2025년 6월 1일에 들었고 2026년 6월 1일 만기인데, 오늘 해지하면 이자 얼마예요?",
     [("2025-06-01", "start"), ("2026-06-01", "maturity"), ("2026-10-28", "termination")]),
    ("3월 10일에 500만 원 넣었는데 다음 달 20일에 깨면 이자 얼마나 나와요?", [("2026-03-10", None), ("2026-11-20", "termination")]),   # '넣었'이 멀어서 모른다
    ("4월 2일에 1,000만 원 가입했고 만기는 내년 4월 2일이에요, 10월 15일에 해지하면 얼마 받아요?",
     [("2026-04-02", None), ("2027-04-02", "maturity"), ("2026-10-15", "termination")]),
    ("2025년 9월 15일에 가입해서 2026년 9월 15일 만기인 700만 원 예금, 7월 말에 해지하면", [("2025-09-15", "start"), ("2026-09-15", "maturity")]),
    ("2026-02-01에 가입하고 2026-09-30에 해지할래요", [("2026-02-01", "start"), ("2026-09-30", "termination")]),
    ("신규일은 2026-01-01이고 만기는 2027-01-01", [("2026-01-01", "start"), ("2027-01-01", "maturity")]),
    ("원금 10,000,000원, 신규 2026-01-01, 만기 2027-01-01, 해지 2026-05-31.",
     [("2026-01-01", "start"), ("2027-01-01", "maturity"), ("2026-05-31", "termination")]),
    ("어제 가입했어요", [("2026-10-27", "start")]),
    ("2026-05-31에 중도해지 하면", [("2026-05-31", "termination")]),
    ("해지일은 2026-12-07", [("2026-12-07", "termination")]),
    ("만기: 2027-01-01", [("2027-01-01", "maturity")]),
])
def test_roles_are_read_from_a_label_before_or_a_keyword_right_after_the_date(text, expected):
    assert roles(text) == expected


@pytest.mark.parametrize("text,expected", [
    # 이 표현들에서 잘못 읽으면 말한 값을 거짓으로 거절한다. 그래서 모르는 것은 None으로 둔다
    ("10만원을 2026-01-01부터 2027-01-01까지 넣었다가 2026-05-31에 해지하면", [("2026-01-01", "start"), ("2027-01-01", None), ("2026-05-31", "termination")]),
    ("2년 만기(2026-01-01 ~ 2028-01-01)로 1000만원 넣었고 기본금리는 4%예요. 2026-10-28에 깨면",
     [("2026-01-01", None), ("2028-01-01", None), ("2026-10-28", "termination")]),                 # 범위 괄호는 라벨이 아니다
    ("2000만원을 2026-03-15에 1년 만기(2027-03-15)로 넣었어요. 오늘 해지하면", [("2026-03-15", None), ("2027-03-15", "maturity"), ("2026-10-28", "termination")]),
    ("2026년 1월 1일에 천만 원을 1년 만기로 넣었는데 2026년 3월 2일에 해지하면", [("2026-01-01", None), ("2026-03-02", "termination")]),   # '1년 만기'는 기간
    ("해지 날짜를 오늘로 잡으면 세금 떼고 실제로 얼마 들어오는지", [("2026-10-28", None)]),
])
def test_ambiguous_phrases_leave_the_role_unknown_instead_of_guessing(text, expected):
    assert roles(text) == expected


@pytest.mark.parametrize("text", ["2026-01-01", "2026-01-01이에요", "그 날은 2026-01-01입니다", "만기가 다가와서 그런데 2027-01-01이래요"])
def test_no_keyword_means_no_role(text):
    assert [m.role for m in date_mentions(text, TODAY)] == [None]


def test_a_label_right_before_the_date_wins_over_a_keyword_after_it():
    assert roles("만기 2027-01-01에 해지") == [("2027-01-01", "maturity")]
    assert roles("2026-12-07에 해지해요, 만기는 모르겠어요") == [("2026-12-07", "termination")]


def test_roles_do_not_cross_lines():
    assert [m.role for m in date_mentions("2026-01-01\n해지하면 얼마예요", TODAY)] == [None]
    assert [m.role for m in date_mentions("만기\n2026-01-01", TODAY)] == [None]


@pytest.mark.parametrize("text,span", [("2026-01-01\n해지하면", "2026-01-01"), ("2026년 1월 1일 에", "2026년 1월 1일"), ("2026-01-01 에", "2026-01-01"),
                                       ("2026-01-01 일", "2026-01-01 일")])
def test_a_date_does_not_swallow_the_whitespace_or_line_break_after_it(text, span):
    m = find_mentions(text, TODAY)[0]
    assert text[m.start:m.end] == span


@pytest.mark.parametrize("text,role", [("어제 가입했어요", "start"), ("2026-01-01 가입했어요", "start"), ("2026-01-01에 가입했어요", "start"),
                                        ("2026-01-01이 만기예요", "maturity"), ("2026-01-01은 해지일이에요", "termination"), ("2026-01-01을 해지해요", "termination")])
def test_a_particle_is_only_a_particle_when_a_space_follows(text, role):
    assert date_mentions(text, TODAY)[0].role == role


def test_a_label_written_after_the_date_is_not_reused_as_the_next_dates_label():
    """'2026-01-01 신규 2027-01-01 만기, 2026-12-07 해지하면': 날짜 뒤에 라벨을 쓰는 표기 (실제로 쓰이는 표기)."""
    assert roles("1000만원 2026-01-01 신규 2027-01-01 만기, 2026-12-07 해지하면 이자는?") == [
        ("2026-01-01", "start"), ("2027-01-01", "maturity"), ("2026-12-07", "termination")]
    assert roles("2026-01-01 신규 2027-01-01 만기 2026-05-31 해지") == [("2026-01-01", "start"), ("2027-01-01", "maturity"), ("2026-05-31", "termination")]
    assert roles("2026-01-01 가입 2027-01-01 만기") == [("2026-01-01", "start"), ("2027-01-01", "maturity")]
    # 라벨을 날짜 앞에 쓰는 표기도 그대로
    assert roles("신규 2026-01-01, 만기 2027-01-01, 해지 2026-12-07") == [("2026-01-01", "start"), ("2027-01-01", "maturity"), ("2026-12-07", "termination")]
