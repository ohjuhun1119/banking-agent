"""검증용(held_out) 문항 관리 (10일차): 시스템을 모르는 사람이 쓴 문항을 받아 들이고, 정답을 쓴 뒤 고정하고, 고정이 깨지지 않았는지 확인한다.

흐름
  1) python eval/heldout_tools.py intake 받은문항.txt --writer "누가 썼는지"   한 줄에 한 문항. eval/questions.jsonl에 split=held_out, 정답 없음(unlabeled)으로 추가한다.
  2) 정답(gold)은 측정 전에 질문만 보고 쓴다 (분류 · 행동 · 도구 · 값). 시스템의 답을 보고 고치지 않는다.
  3) python eval/heldout_tools.py freeze --labeler "정답을 쓴 사람"             정답이 모두 있는지 확인하고 지문(sha256)을 eval/held_out_manifest.json에 기록한다. 한 번만 할 수 있다.
  4) python eval/run_eval.py --split held_out --save ...                         고정이 깨지지 않았을 때만 실행되고, 실행은 마일스톤마다 한 번만 허용한다 (--allow-repeat로 예외).
  python eval/heldout_tools.py show [--ids X001,X002]   문항과 정답을 읽기 쉽게 보여 준다 (고정하기 전에 정답을 검토할 때)
  python eval/heldout_tools.py writer "작성자"          작성자 칸을 고친다 (고정 지문에 들어 있지 않아 고정 뒤에도 고칠 수 있다)
  python eval/heldout_tools.py status | verify
고정이 필요한 이유: 문항이나 정답을 보고 고치면 그 문항은 개발용이 된다 (6일차 검증용 20문항이 그랬다).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.build_questions import BEHAVIORS, CATEGORIES, TODAY  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.jsonl"
MANIFEST = ROOT / "eval" / "held_out_manifest.json"
FIELDS = ("question", "today", "category", "gold")      # 고정 지문에 들어가는 칸. writer · note · tags는 빠져 있어 고정 뒤에도 고칠 수 있다
FIELD_LABELS = {"principal": "원금", "start_date": "신규일", "maturity_date": "만기일", "termination_date": "해지일", "base_rate": "기본금리"}


def load_rows(path=None) -> list[dict]:
    path = Path(path or QUESTIONS)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def save_rows(rows: list[dict], path=None) -> None:
    Path(path or QUESTIONS).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def fingerprint(q: dict) -> str:
    return hashlib.sha256(json.dumps({k: q.get(k) for k in FIELDS}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def clean_lines(raw: str) -> list[str]:
    """받은 글에서 문항만 뽑는다: 번호 · 글머리표를 떼고 빈 줄과 같은 문항을 건너뛴다."""
    out, seen = [], set()
    for line in raw.splitlines():
        t = re.sub(r"^\s*(?:\d+\s*[.)\]:]|[-*•·])\s*", "", line).strip()
        key = " ".join(t.split())
        if t and key not in seen:
            seen.add(key)
            out.append(t)
    return out


def intake(raw: str, writer: str, path=None, manifest=None) -> list[dict]:
    """받은 문항을 split=held_out, 정답 없음으로 추가한다. 이미 고정된 뒤에는 받지 않는다 (고정 전에 문항을 다 받아야 한다)."""
    manifest = Path(manifest or MANIFEST)
    if manifest.exists():
        raise RuntimeError("이미 고정되었습니다. 문항을 더 받으려면 새 마일스톤의 검증용으로 별도 파일을 쓰세요.")
    rows = load_rows(path)
    have = {" ".join(r["question"].split()) for r in rows}
    n = max([int(r["id"][1:]) for r in rows if r["id"].startswith("X") and r["id"][1:].isdigit()] or [0])
    added = []
    for t in clean_lines(raw):
        if " ".join(t.split()) in have:
            continue
        n += 1
        added.append({"id": f"X{n:03d}", "split": "held_out", "core": False, "source": ["outsider"], "category": None, "question": t, "today": TODAY, "gold": None,
                      "tags": [], "note": "", "writer": writer})
    save_rows(rows + added, path)
    return added


def problems_labeling(rows: list[dict]) -> list[str]:
    """검증용 문항 중 정답이 덜 쓰인 것."""
    out = []
    for r in rows:
        if r.get("split") != "held_out":
            continue
        g = r.get("gold")
        if not g:
            out.append(f"{r['id']}: 정답(gold)이 없습니다")
        elif r.get("category") not in CATEGORIES:
            out.append(f"{r['id']}: category가 올바르지 않습니다 ({r.get('category')})")
        elif g.get("behavior") not in BEHAVIORS or not isinstance(g.get("tools"), list):
            out.append(f"{r['id']}: gold.behavior · gold.tools가 올바르지 않습니다")
    return out


def freeze(labeler: str, path=None, manifest=None, today: str | None = None) -> dict:
    manifest = Path(manifest or MANIFEST)
    if manifest.exists():
        raise RuntimeError("이미 고정되었습니다 (eval/held_out_manifest.json). 다시 고정할 수 없습니다.")
    rows = load_rows(path)
    held = [r for r in rows if r.get("split") == "held_out"]
    if not held:
        raise RuntimeError("검증용 문항이 없습니다.")
    problems = problems_labeling(rows)
    if problems:
        raise RuntimeError("정답이 덜 쓰였습니다:\n  " + "\n  ".join(problems))
    data = {"frozen_on": today or date.today().isoformat(), "labeler": labeler, "count": len(held), "fingerprints": {r["id"]: fingerprint(r) for r in held}, "runs": []}
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return data


def verify(path=None, manifest=None) -> list[str]:
    """고정이 깨졌는지: 빈 목록이면 이상 없음."""
    manifest = Path(manifest or MANIFEST)
    if not manifest.exists():
        return ["고정되지 않았습니다 (python eval/heldout_tools.py freeze)"]
    data = json.loads(manifest.read_text(encoding="utf-8"))
    held = {r["id"]: r for r in load_rows(path) if r.get("split") == "held_out"}
    out = []
    for qid, fp in data["fingerprints"].items():
        if qid not in held:
            out.append(f"{qid}: 고정된 문항이 사라졌습니다")
        elif fingerprint(held[qid]) != fp:
            out.append(f"{qid}: 고정 뒤에 문항 또는 정답이 바뀌었습니다")
    out += [f"{qid}: 고정 뒤에 문항이 추가되었습니다" for qid in held if qid not in data["fingerprints"]]
    return out


def _held_out(path=None, ids=None) -> list[dict]:
    return [r for r in load_rows(path) if r.get("split") == "held_out" and (not ids or r["id"] in ids)]


def show(path=None, ids=None) -> str:
    """검증용 문항과 정답을 읽기 쉽게: 고정하기 전에 정답을 검토할 때 쓴다."""
    out = []
    for r in _held_out(path, ids):
        g = r.get("gold") or {}
        lines = [f"{r['id']} [{r.get('category')}] {g.get('behavior', '(정답 없음)')} | {r['question']}"]
        if g:
            tools = "필수 도구: " + (", ".join(g.get("tools") or []) or "없음")
            lines.append("   " + tools + (f" / 불러도 되는 도구: {', '.join(g['tools_allowed'])}" if g.get("tools_allowed") else ""))
            c = g.get("calc")
            if c:
                args = ", ".join(f"{FIELD_LABELS.get(k, k)} {v}" for k, v in c["args"].items()) or "(없음)"
                lines.append(f"   계산 입력: {args} → 상태 {c['status']}" + (f" → 이자 {c['interest_krw']:,}원" if c.get("interest_krw") is not None else ""))
            if g.get("missing"):
                lines.append("   되물을 칸: " + ", ".join(g["missing"]))
            if g.get("unsupported"):
                lines.append("   날짜가 정확하지 않아 되물어야 하는 칸: " + ", ".join(FIELD_LABELS.get(k, k) for k in g["unsupported"]))
            if g.get("chunks"):
                lines.append("   정답 청크(하나라도 근거 카드에 있어야 함): " + ", ".join(g["chunks"]))
            if g.get("traps"):
                lines.append("   함정 청크(정답보다 앞서면 안 됨): " + ", ".join(g["traps"]))
            if g.get("must_say_any"):
                lines.append("   답변이나 근거 카드에 있어야 할 말(하나): " + " · ".join(g["must_say_any"]))
            if g.get("must_not"):
                lines.append("   답변에 있으면 안 되는 말: " + " · ".join(g["must_not"]))
            if g.get("weak_check"):
                lines.append("   (느슨한 채점: 계산 · 상담 저장만 금지)")
        if r.get("note"):
            lines.append("   해석: " + r["note"])
        lines.append(f"   작성자: {r.get('writer') or '(없음)'}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def set_writer(writer: str, path=None, ids=None) -> int:
    """검증용 문항의 작성자 칸을 고친다 (고정 지문에 안 들어 있어 고정 뒤에도 된다). 고친 문항 수를 돌려준다."""
    if not writer.strip():
        raise RuntimeError("작성자를 비울 수 없습니다.")
    rows, n = load_rows(path), 0
    for r in rows:
        if r.get("split") == "held_out" and (not ids or r["id"] in ids):
            r["writer"], n = writer.strip(), n + 1
    if n:
        save_rows(rows, path)
    return n


def runs(manifest=None) -> list[dict]:
    manifest = Path(manifest or MANIFEST)
    return json.loads(manifest.read_text(encoding="utf-8")).get("runs", []) if manifest.exists() else []


def record_run(save_path: str, summary: dict, manifest=None, today: str | None = None) -> None:
    manifest = Path(manifest or MANIFEST)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["runs"].append({"date": today or date.today().isoformat(), "file": save_path, "behavior_ok": summary["overall"].get("behavior_ok"), "clean": summary["overall"].get("clean")})
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def status(path=None, manifest=None) -> str:
    manifest = Path(manifest or MANIFEST)
    rows = load_rows(path)
    held = [r for r in rows if r.get("split") == "held_out"]
    lines = [f"검증용 문항 {len(held)}개 (정답 있음 {sum(bool(r.get('gold')) for r in held)}개)"]
    if manifest.exists():
        bad = verify(path, manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        lines.append(f"고정됨 {data['frozen_on']} (정답 작성: {data['labeler']}), 실행 {len(data['runs'])}회, " + ("이상 없음" if not bad else f"문제 {len(bad)}건: " + "; ".join(bad)))
    else:
        lines.append("아직 고정되지 않았습니다")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="검증용 문항 받기 · 고정 · 확인")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("intake"); p.add_argument("file"); p.add_argument("--writer", required=True, help="누가 썼는지 (예: 친구 A, 시스템을 모름)")
    p = sub.add_parser("freeze"); p.add_argument("--labeler", required=True, help="정답을 쓴 사람")
    p = sub.add_parser("show"); p.add_argument("--ids", help="쉼표로 구분한 문항 id")
    p = sub.add_parser("writer"); p.add_argument("name"); p.add_argument("--ids", help="쉼표로 구분한 문항 id (없으면 검증용 전체)")
    sub.add_parser("status")
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "intake":
            added = intake(Path(a.file).read_text(encoding="utf-8"), a.writer)
            print(f"{len(added)}문항을 받았어요 ({added[0]['id']}~{added[-1]['id']}). 이제 정답(gold)을 질문만 보고 쓰세요." if added else "새로 받은 문항이 없어요 (모두 이미 있는 문항).")
        elif a.cmd == "freeze":
            data = freeze(a.labeler)
            print(f"{data['count']}문항을 고정했어요 ({data['frozen_on']}). 이제 문항과 정답을 바꾸지 마세요.")
        elif a.cmd == "show":
            text = show(ids=set(a.ids.split(",")) if a.ids else None)
            print(text or "보여 줄 검증용 문항이 없어요.")
            return 0 if text else 1
        elif a.cmd == "writer":
            n = set_writer(a.name, ids=set(a.ids.split(",")) if a.ids else None)
            print(f"{n}문항의 작성자를 고쳤어요." if n else "고칠 문항이 없어요.")
            return 0 if n else 1
        elif a.cmd == "verify":
            bad = verify()
            print("이상 없음" if not bad else "\n".join(bad))
            return 1 if bad else 0
        else:
            print(status())
    except RuntimeError as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
