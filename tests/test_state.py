"""6일차 상태 도우미 시험: 확인된 값 고르기(accepted_values)와 요약(describe_confirmed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.state import accepted_values, describe_confirmed  # noqa: E402

FULL = {"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
        "termination_date": "2026-07-20", "base_rate": 3.5}


def test_valid_values_are_kept_in_json_friendly_types():
    got = accepted_values(FULL)
    assert got == FULL
    assert isinstance(got["principal"], int) and isinstance(got["base_rate"], float) and isinstance(got["start_date"], str)


def test_tool_accepted_formats_are_normalized():
    got = accepted_values({"principal": "10,000,000원", "start_date": "2026-01-01", "base_rate": "3.5%"})
    assert got == {"principal": 10_000_000, "start_date": "2026-01-01", "base_rate": 3.5}


def test_blank_and_missing_values_are_left_out():
    assert accepted_values({"principal": None, "start_date": "", "maturity_date": "  "}) == {}
    assert accepted_values({}) == {}
    assert accepted_values(None) == {}
    assert accepted_values("1000만원") == {}


@pytest.mark.parametrize("args,dropped", [
    ({"principal": "1000만원"}, "principal"),                       # 원 단위 숫자가 아님
    ({"principal": 500_000}, "principal"),                          # 최소 가입금액 미만
    ({"start_date": "20일"}, "start_date"),                         # 날짜 형식이 아님
    ({"start_date": "2026-02-30"}, "start_date"),                   # 없는 날짜
    ({"base_rate": 25}, "base_rate"),                               # 범위 밖
    ({"base_rate": "높음"}, "base_rate"),
    ({"principal": True}, "principal"),
])
def test_invalid_values_are_dropped(args, dropped):
    assert dropped not in accepted_values({**args})


def test_one_bad_field_does_not_drop_the_good_ones():
    got = accepted_values({"principal": "1000만원", "start_date": "2026-01-01", "maturity_date": "2027-01-01"})
    assert got == {"start_date": "2026-01-01", "maturity_date": "2027-01-01"}


def test_cross_field_errors_drop_only_the_offending_field():
    got = accepted_values({"start_date": "2026-05-01", "termination_date": "2026-04-01"})       # 해지일이 신규일보다 앞섬
    assert got == {"start_date": "2026-05-01"}
    got = accepted_values({"start_date": "2026-01-01", "maturity_date": "2026-01-10"})          # 만기가 1개월 미만
    assert got == {"start_date": "2026-01-01"}


def test_unknown_arguments_are_ignored():
    assert accepted_values({"principal": 10_000_000, "days": 150}) == {"principal": 10_000_000}


def test_describe_confirmed():
    assert describe_confirmed({}) == "" and describe_confirmed(None) == ""
    assert describe_confirmed(FULL) == "원금 10,000,000원 · 신규일 2026-01-01 · 만기일 2027-01-01 · 해지일 2026-07-20 · 기본금리 3.5%"
    assert describe_confirmed({"termination_date": "2026-07-20", "principal": 5_000_000}) == "원금 5,000,000원 · 해지일 2026-07-20"
