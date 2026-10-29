"""검증용 문항(eval/heldout/questions.json) 20개를 실제 모델(에이전트)로 돌려 행동을 확인한다. Ollama가 켜져 있어야 한다.

질문마다 새 대화(세션)로 한 번씩 묻고, 오늘 날짜는 questions.json의 today(2026-10-28)로 고정한다.
채점은 거칠고 객관적인 것만 한다 (정답 문장을 비교하지 않는다).
  behavior_ok     기대한 행동을 했는가: search=검색 도구를 부름 / calc=계산 도구를 부름 / calc_partial=계산 도구를 부르거나 되물음 /
                  ask=되물음 / search_ask=검색을 부르고 되물음.  '되물음'은 답에 '?' 또는 '알려주'가 있는지로만 본다 (거친 기준)
  fabricated_ok   계산 도구가 status=ok를 돌려줬다. 이 20문항은 모두 값이 부족하거나(되물어야 함), 만기 후 해지이거나, 6개월 이상인데 기본금리가
                  없는 경우라서 ok가 나오면 지어낸 값이 가드를 통과했다는 뜻이다
  foreign_script  답에 한자 · 가나가 섞임 / phone_in_answer 답에 전화번호 모양이 있음(도구 결과에 있는 번호일 수 있어 표시만)
  model_error     모델 호출 실패
실행: python eval/heldout_run.py --save eval/heldout/runs/short_name.json     (--only H1,H6: 일부만)
이 결과를 보고 규칙을 고치지 않는다. 고친 뒤 한 번 더 재서 수정 전후를 기록한다 (questions.json의 rule 참고).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.answer_check import foreign_script, phone_numbers  # noqa: E402
from app.graph import Agent  # noqa: E402

QUESTIONS = ROOT / "eval" / "heldout" / "questions.json"


def load(path: Path = QUESTIONS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def asked_user(answer: str) -> bool:
    return "?" in answer or "알려주" in answer or "알려 주" in answer


def grade(q: dict, res: dict) -> dict:
    trace = res.get("trace") or []
    calc = [t for t in trace if t["name"] == "calc_interest"]
    search = [t for t in trace if t["name"] == "search_terms"]
    ans = res.get("answer") or ""
    asked = asked_user(ans)
    behavior = {"search": bool(search), "calc": bool(calc), "calc_partial": bool(calc) or asked, "ask": asked,
                "search_ask": bool(search) and asked}[q["expect"]]
    flags = []
    if res.get("error"):
        flags.append("model_error")
    elif not behavior:
        flags.append("wrong_behavior")
    if any(t.get("status") == "ok" for t in calc):
        flags.append("fabricated_ok")
    if foreign_script(ans):
        flags.append("foreign_script")
    if phone_numbers(ans):
        flags.append("phone_in_answer")
    return {"behavior_ok": behavior and not res.get("error"), "asked": asked, "flags": flags,
            "dropped_any": any(t.get("dropped") for t in trace)}


def run(agent=None, only: set[str] | None = None, log=print) -> dict:
    data = load()
    agent = agent or Agent()
    rows = []
    for q in data["questions"]:
        if only and q["id"] not in only:
            continue
        res = agent.ask(q["question"], f"heldout-{q['id']}", data["today"])
        g = grade(q, res)
        tools = "; ".join(f"{t['name']}({','.join(f'{k}={v}' for k, v in t['args'].items())})->{t['status']}"
                          + (f"[말하지 않은 값 제외 {','.join(t['dropped'])}]" if t.get("dropped") else "") for t in res.get("trace", [])) or "(도구 없음)"
        rows.append({"id": q["id"], "question": q["question"], "expect": q["expect"], "answer": res.get("answer"), "error": res.get("error"),
                     "stop_reason": res.get("stop_reason"), "trace": res.get("trace", []), "tool_calls": res.get("tool_calls"),
                     "model_calls": res.get("model_calls"), "ms": res.get("ms"), **g})
        if log:
            ans = (res.get("answer") or res.get("error") or "").replace("\n", " ")
            log(f"[{'OK' if g['behavior_ok'] and not g['flags'] else 'NG'}] {q['id']:<3} {q['expect']:<12} {tools}\n      답: {ans[:110]}"
                + (f"\n      표시: {', '.join(g['flags'])}" if g["flags"] else ""))
    flags = Counter(f for r in rows for f in r["flags"])
    summary = {"n": len(rows), "behavior_ok": sum(r["behavior_ok"] for r in rows), "flags": dict(flags),
               "all_clean": sum(1 for r in rows if r["behavior_ok"] and not r["flags"]),
               "by_expect": {e: [sum(r["behavior_ok"] for r in rows if r["expect"] == e), sum(1 for r in rows if r["expect"] == e)]
                             for e in sorted({r["expect"] for r in rows})}}
    return {"kind": "heldout_run", "created": datetime.now().isoformat(timespec="seconds"), "today": data["today"], "frozen_on": data["frozen_on"],
            "rows": rows, "summary": summary}


def print_summary(res: dict, log=print) -> None:
    s = res["summary"]
    log(f"\n검증용 {s['n']}문항: 기대한 행동 {s['behavior_ok']}/{s['n']}, 표시 없이 깨끗한 것 {s['all_clean']}/{s['n']}")
    log("  기대별: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in s["by_expect"].items()))
    log("  표시: " + (", ".join(f"{k} {v}" for k, v in s["flags"].items()) or "없음"))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="검증용 문항을 실제 모델로 돌려 행동을 확인한다")
    ap.add_argument("--save")
    ap.add_argument("--only")
    a = ap.parse_args(argv)
    only = {s.strip() for s in a.only.split(",")} if a.only else None
    res = run(only=only)
    print_summary(res)
    if a.save:
        Path(a.save).parent.mkdir(parents=True, exist_ok=True)
        Path(a.save).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\n저장: {a.save}")


if __name__ == "__main__":
    main()
