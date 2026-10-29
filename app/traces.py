"""요청 트레이스: 질문마다 도구 선택 과정을 SQLite(data/traces.sqlite3)에 남기고, 도구를 잘못 고른 사례를 모은다 (9일차).

왜 로컬 저장소인가: 외부 트레이싱 서비스(LangSmith · Langfuse)는 계정이 필요하고 질문과 도구 결과(약관 본문 포함)를 클라우드로 보낸다.
이 저장소는 계정 없이 쓰고 약관 본문은 저장하지 않는다 (도구 이름 · 인자 · 상태 · 시간만). *.sqlite3는 git에서 제외된다.
저장하는 것: 질문, 답변, 도구 호출 목록(모델이 넘긴 인자 원본 · 코드가 뺀 값 · 채운 값 · 강제 호출 여부), 근거 카드 종류, 신호(signals).
신호는 '잘못 고른 사례 후보'를 기계적으로 표시한다 (사람이 라벨을 붙여 확정한다):
  forced_search / forced_calc   모델이 도구를 건너뛰어 코드가 먼저 불렀다
  dropped:<칸>                  모델이 사용자가 말하지 않은 값을 넣었다 (코드가 뺌)
  filled:<칸>                   모델이 사용자가 말한 값을 빠뜨렸다 (코드가 채움)
  ignored:<인자>                모델이 도구에 없는 인자를 넣었다
  calc_on_rule_question         방법 · 규칙 질문에 모델이 계산 도구를 불렀는데 읽을 수 있는 값이 없다 (검증용 H14 · H17)
  search_on_amount_question     금액을 묻는 질문에 모델이 계산 없이 검색만 했다 (H19 유형)
  tool_error / retried / invalid_input / duplicate_call / handoff_confirmation / tool_limit / model_error / error
명령줄: python -m app.traces list [--signals] | mark ID 라벨 [--note 메모] | export [--label wrong_tool]
설정: TRACE_LOG=0이면 저장하지 않는다. TRACE_DB로 파일 위치를 바꾼다.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.evidence import calc_intent  # noqa: E402
from app.tool_calling import CALC_ARGS  # noqa: E402

DB_PATH = Path(os.environ.get("TRACE_DB", ROOT / "data" / "traces.sqlite3"))
LABELS = ("ok", "wrong_tool", "other")
EXCERPT_CHARS = 120               # 내보낼 때 답변은 이만큼만 (약관 내용을 길게 옮기지 않는다)
SCHEMA = """CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, session_id TEXT, question TEXT NOT NULL, answer TEXT, stop_reason TEXT, error TEXT,
    ms INTEGER, model_calls INTEGER, tool_calls INTEGER, tools TEXT NOT NULL, evidence_kinds TEXT, signals TEXT NOT NULL, label TEXT, note TEXT)"""
TOOL_KEYS = ("name", "args", "status", "ms", "forced", "forced_kind", "retried", "duplicate", "dropped", "filled", "ignored")


def enabled() -> bool:
    return os.environ.get("TRACE_LOG", "1") != "0"


def signals_of(res: dict, question: str) -> list[str]:
    """한 질문의 결과(Agent.ask의 반환값)에서 '도구를 잘못 고른 사례 후보' 신호를 뽑는다."""
    out: list[str] = []
    trace = res.get("trace") or []
    for t in trace:
        if t.get("forced") and t.get("forced_kind") in ("search", "calc"):
            out.append(f"forced_{t['forced_kind']}")
        if t.get("dropped"):
            out.append("dropped:" + ",".join(sorted(t["dropped"])))
        if t.get("ignored"):
            out.append("ignored:" + ",".join(sorted(t["ignored"])))
        if t.get("filled") and not t.get("forced"):
            out.append("filled:" + ",".join(sorted(t["filled"])))
        if t.get("status") == "error":
            out.append("tool_error")
        if t.get("retried"):
            out.append("retried")
        if t.get("status") == "invalid_input":
            out.append("invalid_input")
        if t.get("duplicate"):
            out.append("duplicate_call")
        if t.get("status") == "needs_confirmation":
            out.append("handoff_confirmation")
    for t in trace:                                           # 모델이 직접 부른 계산 도구에 읽을 수 있는 값이 없는데 방법 · 규칙을 묻는 질문
        if t.get("name") == "calc_interest" and not t.get("forced"):
            given = {a for a in (t.get("args") or {}) if a in CALC_ARGS}
            effective = (given - set(t.get("dropped") or {})) | set(t.get("filled") or {})
            if effective <= {"termination_date"} and not calc_intent(question):
                out.append("calc_on_rule_question")
    names = {t.get("name") for t in trace}
    if calc_intent(question) and "calc_interest" not in names and "search_terms" in names:
        out.append("search_on_amount_question")
    if res.get("stop_reason") in ("tool_limit", "model_error"):
        out.append(res["stop_reason"])
    if res.get("error"):
        out.append("error")
    return list(dict.fromkeys(out))


def _connect(db_path=None):
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.execute(SCHEMA)
    return con


def record(res: dict, question: str, db_path=None, now: datetime | None = None) -> int | None:
    """질문 하나의 결과를 저장하고 트레이스 번호를 돌려준다. 저장에 실패하거나 꺼져 있으면 None (요청을 막지 않는다)."""
    if not enabled():
        return None
    try:
        tools = [{k: t.get(k) for k in TOOL_KEYS} for t in (res.get("trace") or [])]
        with closing(_connect(db_path)) as con:
            cur = con.execute(
                "INSERT INTO traces (created_at, session_id, question, answer, stop_reason, error, ms, model_calls, tool_calls, tools, evidence_kinds, signals) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ((now or datetime.now()).isoformat(timespec="seconds"), res.get("session_id"), question, res.get("answer"), res.get("stop_reason"), res.get("error"),
                 res.get("ms"), res.get("model_calls"), res.get("tool_calls"), json.dumps(tools, ensure_ascii=False, default=str),
                 ",".join(c.get("kind", "") for c in res.get("evidence") or []), json.dumps(signals_of(res, question), ensure_ascii=False)))
            con.commit()
            return cur.lastrowid
    except Exception:                                         # 트레이스가 요청을 망가뜨리면 안 된다
        return None


def _row(r) -> dict:
    keys = ("id", "created_at", "session_id", "question", "answer", "stop_reason", "error", "ms", "model_calls", "tool_calls", "tools", "evidence_kinds", "signals", "label", "note")
    d = dict(zip(keys, r))
    d["tools"], d["signals"] = json.loads(d["tools"]), json.loads(d["signals"])
    return d


def list_traces(limit: int = 20, only_signals: bool = False, label: str | None = None, db_path=None) -> list[dict]:
    """최근 트레이스 (최신순). only_signals: 신호가 있는 것만. label: 그 라벨만."""
    if not Path(db_path or DB_PATH).exists():
        return []
    with closing(_connect(db_path)) as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM traces ORDER BY id DESC")]
    if only_signals:
        rows = [r for r in rows if r["signals"]]
    if label:
        rows = [r for r in rows if r["label"] == label]
    return rows[:limit]


def mark(trace_id: int, label: str, note: str = "", db_path=None) -> bool:
    """트레이스에 라벨(ok · wrong_tool · other)과 메모를 붙인다. 없는 번호이거나 잘못된 라벨이면 False."""
    if label not in LABELS or not Path(db_path or DB_PATH).exists():
        return False
    with closing(_connect(db_path)) as con:
        cur = con.execute("UPDATE traces SET label = ?, note = ? WHERE id = ?", (label, note or None, trace_id))
        con.commit()
        return cur.rowcount == 1


def _tool_line(t: dict) -> str:
    args = ", ".join(f"{k}={v}" for k, v in (t.get("args") or {}).items())
    extra = (f" [뺀 값 {','.join(sorted(t['dropped']))}]" if t.get("dropped") else "") + (f" [채운 값 {','.join(sorted(t['filled']))}]" if t.get("filled") else "") \
        + (f" [코드가 먼저 {t['forced_kind']}]" if t.get("forced") else "")
    return f"{t['name']}({args}) → {t['status']}{extra}"


def export_markdown(label: str = "wrong_tool", db_path=None) -> str:
    """라벨이 붙은 사례를 README · 노션에 옮기기 쉬운 마크다운으로. 답변은 앞부분만 싣는다 (약관 내용을 길게 옮기지 않는다)."""
    rows = list(reversed(list_traces(limit=10_000, label=label, db_path=db_path)))
    lines = [f"# 도구 선택 사례 ({label}, {len(rows)}건)", ""]
    for r in rows:
        lines += [f"### #{r['id']} · {r['created_at']}", f"- 질문: {r['question']}", "- 도구: " + (" / ".join(_tool_line(t) for t in r["tools"]) or "(도구 없음)"),
                  f"- 신호: {', '.join(r['signals']) or '없음'}", f"- 답변(앞부분): {(r['answer'] or '')[:EXCERPT_CHARS].replace(chr(10), ' ')}"]
        if r["note"]:
            lines.append(f"- 메모: {r['note']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="요청 트레이스 보기 · 라벨 · 내보내기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--signals", action="store_true", help="신호가 있는 것만"); p.add_argument("--limit", type=int, default=20); p.add_argument("--label")
    p = sub.add_parser("mark"); p.add_argument("id", type=int); p.add_argument("label", choices=LABELS); p.add_argument("--note", default="")
    p = sub.add_parser("export"); p.add_argument("--label", default="wrong_tool", choices=LABELS)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for r in list_traces(a.limit, a.signals, a.label):
            print(f"#{r['id']} {r['created_at']} [{r['label'] or '-'}] {r['question'][:50]} | 도구 {len(r['tools'])} | 신호: {', '.join(r['signals']) or '-'}")
    elif a.cmd == "mark":
        print("라벨을 붙였어요." if mark(a.id, a.label, a.note) else "붙이지 못했어요 (없는 번호이거나 저장소가 없어요).")
    else:
        print(export_markdown(a.label))


if __name__ == "__main__":
    main()
