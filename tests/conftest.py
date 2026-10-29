"""모든 시험이 실제 상담 요청 DB(data/handoff.sqlite3)를 건드리지 않게 임시 경로를 쓴다."""
import pytest


@pytest.fixture(autouse=True)
def _temporary_handoff_db(tmp_path, monkeypatch):
    from app.tools import handoff
    monkeypatch.setattr(handoff, "DB_PATH", tmp_path / "handoff.sqlite3")
