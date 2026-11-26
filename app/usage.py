"""API 모델 하루 호출 상한: 돈이 나가는 모델 호출을 하루 단위로 세고(SQLite, 서버를 다시 띄워도 유지), 상한을 넘으면 모델을 부르지 않게 한다.

날짜는 한국 시간 기준이다 (서버는 UTC라 한국 새벽 0~9시에도 날짜가 한국에서 바뀐다). 로컬 모델에는 쓰지 않는다.
LLM_DAILY_CALL_LIMIT: 정하지 않으면 300, 0이면 상한 없음. 질문 하나는 보통 모델 호출 2번이다.
shortcut: 하루 전체를 한 칸으로 센다 (사용자별 · 시간대별 한도 없음). 필요해지면 키를 늘린다.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("LLM_USAGE_DB", ROOT / "data" / "llm_usage.sqlite3"))
DEFAULT_LIMIT = 300
KST = dt.timezone(dt.timedelta(hours=9))


def today() -> str:
    return dt.datetime.now(KST).date().isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)          # 트랜잭션은 직접 관리한다
    con.execute("create table if not exists usage(day text primary key, calls integer not null)")
    return con


def reserve(limit: int, day: str | None = None) -> tuple[bool, int]:
    """호출 한 번을 세어 본다. (허용 여부, 지금까지 센 호출 수). 상한에 닿았으면 세지 않고 (False, 상한)을 돌려준다. 동시에 여러 요청이 와도 상한을 넘지 않는다."""
    day = day or today()
    with closing(_connect()) as con:
        con.execute("begin immediate")                                         # 쓰기 잠금: 확인과 증가가 한 덩어리
        con.execute("insert or ignore into usage(day, calls) values (?, 0)", (day,))
        updated = con.execute("update usage set calls = calls + 1 where day = ? and calls < ?", (day, limit)).rowcount
        calls = con.execute("select calls from usage where day = ?", (day,)).fetchone()[0]
        con.execute("commit")
        return updated == 1, calls


def used(day: str | None = None) -> int:
    if not DB_PATH.exists():
        return 0
    with closing(_connect()) as con:
        row = con.execute("select calls from usage where day = ?", (day or today(),)).fetchone()
        return row[0] if row else 0


def daily_limit() -> int | None:
    """None이면 상한 없음. 잘못된 값은 조용히 무제한이 되지 않게 오류로 알린다."""
    raw = (os.environ.get("LLM_DAILY_CALL_LIMIT") or "").strip()
    if not raw:
        return DEFAULT_LIMIT
    try:
        n = int(raw)
    except ValueError:
        n = -1
    if n < 0:
        raise ValueError(f"LLM_DAILY_CALL_LIMIT는 0 이상의 정수여야 합니다 (0 = 상한 없음): {raw!r}")
    return None if n == 0 else n
