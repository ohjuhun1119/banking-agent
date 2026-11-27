"""운영자용 상담 기록: CHAT_LOG_DB를 정했을 때만 질문과 답을 개인정보를 가린 채 SQLite에 남긴다 (안 정하면 아무것도 저장하지 않는다).

CHAT_LOG_DAYS(기본 30일)가 지난 줄은 다음 기록 때 지운다. 화면에는 노출하지 않고 서버에서만 본다:
  docker compose -f compose.yaml -f compose.prod.yaml exec api python -m app.history [개수]
shortcut: 사용자 구분 없이 세션 ID로만 묶는다, 사용자별 조회가 필요해지면 로그인 구분부터 만든다.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from app.redact import mask

DEFAULT_DAYS = 30


def _path() -> Path | None:
    raw = (os.environ.get("CHAT_LOG_DB") or "").strip()
    return Path(raw) if raw else None


def _days() -> int:
    raw = (os.environ.get("CHAT_LOG_DAYS") or "").strip()
    if not raw:
        return DEFAULT_DAYS
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError(f"CHAT_LOG_DAYS는 1 이상의 정수여야 합니다: {raw!r}")
    return n


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("create table if not exists log(id integer primary key, at text not null, session_id text not null, question text not null, "
                "answer text not null, stop_reason text, ms integer)")
    return con


def record(session_id: str, question: str, answer: str, stop_reason: str | None, ms: int | None) -> bool:
    """한 번의 질문과 답을 남긴다. 기록이 꺼져 있으면 False."""
    path = _path()
    if path is None:
        return False
    keep = _days()
    now = dt.datetime.now(dt.timezone.utc)
    with closing(_connect(path)) as con, con:
        con.execute("delete from log where at < ?", ((now - dt.timedelta(days=keep)).isoformat(timespec="seconds"),))
        con.execute("insert into log(at, session_id, question, answer, stop_reason, ms) values (?, ?, ?, ?, ?, ?)",
                    (now.isoformat(timespec="seconds"), session_id, mask(question), mask(answer or ""), stop_reason, ms))
    return True


def recent(limit: int = 50) -> list[dict]:
    path = _path()
    if path is None or not path.exists():
        return []
    with closing(_connect(path)) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("select at, session_id, question, answer, stop_reason, ms from log order by id desc limit ?", (limit,))]


def report(rows: list[dict]) -> str:
    if not rows:
        return "상담 기록이 없습니다."
    out = [f"상담 기록 {len(rows)}건 (최근 순)"]
    for r in rows:
        out += ["", f"[{r['at']}] 세션 {r['session_id']} · {r['ms']}ms" + (f" · {r['stop_reason']}" if r["stop_reason"] else ""), f"  Q: {r['question']}", f"  A: {r['answer']}"]
    return "\n".join(out)


if __name__ == "__main__":
    print(report(recent(int(sys.argv[1]) if len(sys.argv) > 1 else 50)))
