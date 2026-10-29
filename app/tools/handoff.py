"""상담 요청 저장 (request_handoff): 질문과 확인된 값을 SQLite에 저장하고, 저장에 성공했을 때만 접수번호를 돌려준다.

원칙
  - '접수됐다'는 말은 저장이 성공한 뒤에 코드가 만든 문장으로만 한다 (모델이 쓰지 않는다). 저장 실패는 실패라고 알린다.
  - 요약은 코드가 만든다: 사용자가 한 질문 몇 개와 확인된 값. 모델이 요약을 쓰면 지어낼 수 있다.
  - 이름 · 연락처 · 계좌번호는 받지 않는다. 사용자가 질문에 적은 글자와 확인된 금융 값만 저장한다 (로컬 파일, git 제외).
  - 실제 상담원에게 전달되지는 않는다 (이 프로젝트에는 상담원 시스템이 없다). 안내 문구가 이를 밝힌다.
저장 위치: data/handoff.sqlite3 (환경변수 HANDOFF_DB로 바꿀 수 있다). 시험은 임시 경로를 쓴다.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("HANDOFF_DB", ROOT / "data" / "handoff.sqlite3"))
MAX_MESSAGES = 3                    # 요약에 넣는 최근 질문 수
MAX_MESSAGE_CHARS = 200             # 질문 하나당 글자 수 상한
SCHEMA = """CREATE TABLE IF NOT EXISTS handoff_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, session_id TEXT, summary TEXT NOT NULL,
    messages TEXT NOT NULL, confirmed TEXT, status TEXT NOT NULL DEFAULT 'open')"""


def _clean(messages) -> list[str]:
    return [m.strip()[:MAX_MESSAGE_CHARS] for m in (messages or []) if isinstance(m, str) and m.strip()][-MAX_MESSAGES:]


def build_summary(user_messages, confirmed_text: str = "") -> str:
    """상담원이 읽을 요약: 사용자 질문(최근 몇 개)과 확인된 값."""
    parts = ["사용자 질문: " + " / ".join(_clean(user_messages))]
    if confirmed_text:
        parts.append("확인된 값: " + confirmed_text)
    return "\n".join(parts)


def request_handoff(user_messages, confirmed_text: str = "", session_id: str = "", db_path=None, now: datetime | None = None) -> dict:
    """상담 요청을 저장한다. 반환 status: saved(ticket_id) / invalid_input(저장할 질문 없음) / error(저장 실패). 예외는 내지 않는다."""
    msgs = _clean(user_messages)
    if not msgs:
        return {"status": "invalid_input", "message": "저장할 질문이 없습니다."}
    now = now or datetime.now()
    summary = build_summary(msgs, confirmed_text)
    try:
        path = Path(db_path or DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5)) as con:
            con.execute(SCHEMA)
            cur = con.execute("INSERT INTO handoff_requests (created_at, session_id, summary, messages, confirmed) VALUES (?, ?, ?, ?, ?)",
                              (now.isoformat(timespec="seconds"), session_id or "", summary, json.dumps(msgs, ensure_ascii=False), confirmed_text or ""))
            con.commit()
            rid = cur.lastrowid
    except (sqlite3.Error, OSError) as e:                     # 경로 · 권한 · 잠금 문제: 접수됐다고 말하지 않는다
        return {"status": "error", "message": f"상담 요청을 저장하지 못했습니다 ({type(e).__name__})."}
    return {"status": "saved", "ticket_id": f"{now:%Y%m%d}-{rid:04d}", "summary": summary}


def count_requests(db_path=None) -> int:
    """저장된 상담 요청 수 (시험 · 점검용). 파일이 없으면 0."""
    path = Path(db_path or DB_PATH)
    if not path.exists():
        return 0
    with closing(sqlite3.connect(path, timeout=5)) as con:
        con.execute(SCHEMA)
        return con.execute("SELECT COUNT(*) FROM handoff_requests").fetchone()[0]
