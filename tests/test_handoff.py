"""상담 요청 저장(app/tools/handoff.py) 시험: 저장에 성공했을 때만 접수번호가 나오고, 실패는 예외 없이 실패로 알려야 한다."""
import json
import sqlite3
from datetime import datetime

import pytest

from app.tools import handoff as hf

NOW = datetime(2026, 10, 29, 9, 30, 0)


def rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT created_at, session_id, summary, messages, confirmed, status FROM handoff_requests ORDER BY id").fetchall()
    finally:
        con.close()


def test_a_saved_request_returns_a_ticket_and_keeps_the_summary(tmp_path):
    db = tmp_path / "h.sqlite3"
    r = hf.request_handoff(["일부만 해지할 수 있어요?", "1000만원 넣었어요"], "원금 10,000,000원", "s1", db, NOW)
    assert r["status"] == "saved" and r["ticket_id"] == "20261029-0001" and "일부만 해지할 수 있어요?" in r["summary"]
    (created, session, summary, messages, confirmed, status), = rows(db)
    assert created == "2026-10-29T09:30:00" and session == "s1" and status == "open"
    assert json.loads(messages) == ["일부만 해지할 수 있어요?", "1000만원 넣었어요"] and confirmed == "원금 10,000,000원"
    assert summary == "사용자 질문: 일부만 해지할 수 있어요? / 1000만원 넣었어요\n확인된 값: 원금 10,000,000원"


def test_ticket_numbers_count_up_and_only_the_last_three_messages_are_kept(tmp_path):
    db = tmp_path / "h.sqlite3"
    assert hf.request_handoff(["a"], db_path=db, now=NOW)["ticket_id"] == "20261029-0001"
    r = hf.request_handoff(["1", "2", "3", "4", "5"], db_path=db, now=NOW)
    assert r["ticket_id"] == "20261029-0002" and json.loads(rows(db)[1][3]) == ["3", "4", "5"]


def test_long_messages_are_cut_and_blank_or_non_text_ones_are_ignored(tmp_path):
    db = tmp_path / "h.sqlite3"
    hf.request_handoff(["가" * 500, "  ", None, 3, "나"], db_path=db, now=NOW)
    assert json.loads(rows(db)[0][3]) == ["가" * hf.MAX_MESSAGE_CHARS, "나"]


@pytest.mark.parametrize("messages", [[], None, ["", "   "], [None, 5]])
def test_nothing_to_save_is_reported_and_nothing_is_stored(tmp_path, messages):
    db = tmp_path / "h.sqlite3"
    r = hf.request_handoff(messages, db_path=db, now=NOW)
    assert r["status"] == "invalid_input" and "ticket_id" not in r and hf.count_requests(db) == 0


def test_a_storage_failure_is_reported_as_an_error_without_a_ticket_and_without_raising(tmp_path):
    broken = tmp_path / "dir"
    broken.mkdir()                                                       # 파일이 아니라 폴더라 sqlite가 열지 못한다
    r = hf.request_handoff(["상담 원해요"], db_path=broken, now=NOW)
    assert r["status"] == "error" and "ticket_id" not in r and "저장하지 못했습니다" in r["message"]
    assert str(tmp_path) not in r["message"]                             # 경로를 사용자에게 흘리지 않는다


def test_a_failure_inside_the_database_call_is_also_an_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(hf.sqlite3, "connect", boom)
    r = hf.request_handoff(["상담 원해요"], db_path=tmp_path / "h.sqlite3", now=NOW)
    assert r["status"] == "error" and "OperationalError" in r["message"] and "ticket_id" not in r


def test_the_default_location_is_the_one_the_tests_replaced_and_missing_folders_are_created(tmp_path):
    assert hf.DB_PATH == tmp_path / "handoff.sqlite3"                    # conftest가 실제 data/handoff.sqlite3를 건드리지 않게 바꿔 둔다
    assert hf.request_handoff(["a"], now=NOW)["status"] == "saved" and hf.count_requests() == 1
    nested = tmp_path / "x" / "y" / "h.sqlite3"
    assert hf.request_handoff(["a"], db_path=nested, now=NOW)["status"] == "saved" and nested.exists()


def test_the_table_has_no_personal_fields(tmp_path):
    db = tmp_path / "h.sqlite3"
    hf.request_handoff(["a"], db_path=db, now=NOW)
    cols = [c[1] for c in sqlite3.connect(db).execute("PRAGMA table_info(handoff_requests)")]
    assert cols == ["id", "created_at", "session_id", "summary", "messages", "confirmed", "status"]       # 이름 · 연락처 · 계좌 칸이 없다


def test_count_is_zero_without_a_file(tmp_path):
    assert hf.count_requests(tmp_path / "none.sqlite3") == 0


def test_summary_format():
    assert hf.build_summary(["a", "b"]) == "사용자 질문: a / b" and hf.build_summary(["a"], "원금 1원").endswith("\n확인된 값: 원금 1원")
