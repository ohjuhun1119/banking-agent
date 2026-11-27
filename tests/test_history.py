"""운영자용 상담 기록: 켜 두었을 때만(CHAT_LOG_DB) 질문과 답을 마스킹해 SQLite에 남기고, 보관 기간이 지난 줄은 지운다."""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import history  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "log.sqlite3"
    monkeypatch.setenv("CHAT_LOG_DB", str(path))
    monkeypatch.delenv("CHAT_LOG_DAYS", raising=False)
    return path


def test_nothing_is_written_unless_the_log_is_switched_on(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAT_LOG_DB", raising=False)
    assert history.record("s1", "질문", "답", None, 10) is False
    assert history.recent() == []


def test_a_recorded_exchange_comes_back_newest_first_with_personal_data_masked(db):
    history.record("s1", "중도해지 이자는요?", "세전 이자는 273,304원이에요.", None, 1200)
    history.record("s2", "제 번호는 010-1234-5678 이에요", "메일 a@b.com 로 보내 드릴 수 없어요.", "off_topic", 30)
    rows = history.recent()
    assert [r["session_id"] for r in rows] == ["s2", "s1"]
    assert "010-1234-5678" not in rows[0]["question"] and "[전화번호]" in rows[0]["question"]
    assert "a@b.com" not in rows[0]["answer"] and "[이메일]" in rows[0]["answer"]
    assert rows[1]["ms"] == 1200 and rows[0]["stop_reason"] == "off_topic"
    assert "010-1234-5678" not in db.read_bytes().decode("latin-1")                 # 원문은 파일에도 남지 않는다


def test_rows_older_than_the_retention_window_are_deleted_on_the_next_write(db, monkeypatch):
    monkeypatch.setenv("CHAT_LOG_DAYS", "30")
    history.record("old", "예전 질문", "예전 답", None, 1)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).isoformat(timespec="seconds")
    con = sqlite3.connect(db)
    con.execute("update log set at = ?", (old,))
    con.commit()
    con.close()
    history.record("new", "새 질문", "새 답", None, 1)
    assert [r["session_id"] for r in history.recent()] == ["new"]


def test_a_bad_retention_value_fails_loudly_instead_of_keeping_everything(db, monkeypatch):
    monkeypatch.setenv("CHAT_LOG_DAYS", "abc")
    with pytest.raises(ValueError):
        history.record("s", "q", "a", None, 1)


def test_the_report_text_lists_each_exchange_and_says_so_when_empty(db):
    assert "기록이 없습니다" in history.report([])
    history.record("s1", "질문입니다", "답입니다", None, 5)
    text = history.report(history.recent())
    assert "s1" in text and "질문입니다" in text and "답입니다" in text
