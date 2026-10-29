"""검증용 문항(eval/heldout/questions.json) 20개를 실제 모델(에이전트)로 돌려 행동을 확인한다. Ollama가 켜져 있어야 한다.

질문마다 새 대화(세션)로 한 번씩 묻고, 오늘 날짜는 questions.json의 today(2026-10-28)로 고정한다.
채점은 객관적으로 셀 수 있는 것만 한다 (정답 문장을 비교하지 않는다). 처음에는 '계산 도구를 불렀는가'만 봐서 기대한 행동 15/20이었지만
답변을 읽어 보니 좋음 4개뿐이었다(관대한 채점). 그래서 답변의 내용을 보는 점검을 더했고, --rescore로 수정 전 기록을 같은 기준으로 다시 채점한다.
  behavior_ok     기대한 행동을 했는가: search=검색 도구를 부름 / calc=계산 도구를 부름 / calc_partial=계산 도구를 부르거나 되물음 /
                  ask=되물음 / search_ask=검색을 부르고 되물음.  '되물음'은 답에 '?' 또는 '알려주'가 있는지로만 본다 (거친 기준)
  fabricated_ok   계산 도구가 status=ok를 돌려줬다. 이 20문항은 모두 값이 부족하거나(되물어야 함), 만기 후 해지이거나, 6개월 이상인데 기본금리가
                  없는 경우라서 ok가 나오면 지어낸 값이 가드를 통과했다는 뜻이다
  foreign_script  답에 한자 · 가나가 섞임 / phone_in_answer 답에 전화번호 모양이 있음(도구 결과에 있는 번호일 수 있어 표시만)
  omitted_stated:<칸>   질문에 적은 값(gold의 said)을 계산 도구에 넣지 않았다 (모델이 빠뜨렸고 코드도 채우지 못함)
  omitted_derived:<칸>  상대 날짜 해석이 필요한 값(gold의 derived)을 넣지 못했다
  reasked_stated:<칸>   이미 말한 값을 답변에서 또 알려 달라고 함
  ungrounded_in_answer  답변에 사용자가 말하지 않은 날짜 · 금액이 있음 (검색 결과가 있는 답변은 도구 결과를 저장하지 않아 점검하지 않음)
  tool_syntax           답변에 도구 호출 문법이 새어 나옴
  unsupported_not_found 검색 결과가 not_found가 아닌데 '약관에서 확인되지 않습니다'라고 함
  asks_format           사용자에게 날짜를 YYYY-MM-DD 형식으로 입력하라고 안내함
  unknown_arg:<이름>    계산 도구에 없는 인자 이름을 넣음 (프롬프트의 내부 낱말을 베낌, H20)
  promises_after_tax    세후 이자를 알려 주겠다고 약속함 (도구는 세전만 계산)
  note_leak             도구의 내부 메모('사용자가 말하지 않은 값')가 답변에 노출됨
  model_error     모델 호출 실패
실행: python eval/heldout_run.py --save eval/heldout/runs/short_name.json     (--only H1,H6: 일부만)
이 결과를 보고 규칙을 고치지 않는다. 고친 뒤 한 번 더 재서 수정 전후를 기록한다 (questions.json의 rule 참고).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.answer_check import foreign_script, not_found_claim, phone_numbers, promises_after_tax, tool_syntax, ungrounded_values  # noqa: E402
from app.graph import Agent  # noqa: E402
from app.tool_calling import CALC_ARGS  # noqa: E402

QUESTIONS = ROOT / "eval" / "heldout" / "questions.json"


def load(path: Path = QUESTIONS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def asked_user(answer: str) -> bool:
    return "?" in answer or "알려주" in answer or "알려 주" in answer


LABELS = {"principal": "원금", "start_date": "신규일", "maturity_date": "만기일", "termination_date": "해지일", "base_rate": "기본금리"}
ASK_WORDS = "알려|입력|언제|얼마|말씀"            # 칸 이름 바로 뒤(조사 한 글자까지)에 이 낱말이 와야 '그 값을 또 물었다'고 본다


def asks_for(sentence: str, label: str) -> bool:
    """문장이 label(칸 이름)의 값을 알려 달라고 하는가. '신규일부터 6개월이 지나' · '신규일 당시 …기본금리는 얼마였나요'는 아니다 (H13 오탐)."""
    return bool(re.search(rf"{label}(?:을|를|은|는|이|가)?\s*(?:{ASK_WORDS})", sentence))


def effective_fields(calls: list[dict]) -> set[str]:
    """계산 도구가 실제로 받은 칸: 모델이 넣은 값에서 코드가 뺀 것을 빼고 코드가 채운 것을 더한다."""
    got: set[str] = set()
    for t in calls:
        got |= {k for k, v in (t.get("args") or {}).items() if v not in (None, "")}
        got -= set((t.get("dropped") or {}))
        got |= set((t.get("filled") or {}))
    return got


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
    gold = q.get("gold") or {}
    if calc:                                                  # 값을 빠뜨렸는지: 계산 도구가 받은 칸과 정답 칸을 비교
        got = effective_fields(calc)
        for fld, v in gold.items():
            if fld not in got:
                flags.append(f"{'omitted_stated' if v['kind'] == 'said' else 'omitted_derived'}:{fld}")
        for sentence in re.split(r"[.?!\n]", ans):           # 이미 말한 값을 또 물었는지
            for fld, v in gold.items():
                if v["kind"] == "said" and asks_for(sentence, LABELS[fld]) and f"reasked_stated:{fld}" not in flags:
                    flags.append(f"reasked_stated:{fld}")
    if ans and not search and not any(t.get("status") == "ok" for t in calc):
        if ungrounded_values(ans, [q.get("question", "")], date.fromisoformat(res.get("today") or "2026-10-28")):
            flags.append("ungrounded_in_answer")
    for t in calc:                                            # 도구에 없는 인자 이름을 넣었는지 (프롬프트의 낱말을 베낀 것)
        for name in sorted(set(t.get("args") or {}) - set(CALC_ARGS)):
            if f"unknown_arg:{name}" not in flags:
                flags.append(f"unknown_arg:{name}")
    if promises_after_tax(ans):
        flags.append("promises_after_tax")
    if tool_syntax(ans):
        flags.append("tool_syntax")
    if not_found_claim(ans) and not any(t.get("status") == "not_found" for t in search):
        flags.append("unsupported_not_found")
    if "YYYY" in ans:
        flags.append("asks_format")
    if "사용자가 말하지 않은 값" in ans:
        flags.append("note_leak")
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
        g = grade(q, {**res, "today": data["today"]})
        tools = "; ".join(f"{t['name']}({','.join(f'{k}={v}' for k, v in t['args'].items())})->{t['status']}"
                          + (f"[말하지 않은 값 제외 {','.join(t['dropped'])}]" if t.get("dropped") else "")
                          + (f"[질문에서 채움 {','.join(t['filled'])}]" if t.get("filled") else "")
                          + (f"[무시한 인자 {','.join(t['ignored'])}]" if t.get("ignored") else "")
                          + ("[코드가 먼저 검색]" if t.get("forced") else "") for t in res.get("trace", [])) or "(도구 없음)"
        rows.append({"id": q["id"], "question": q["question"], "expect": q["expect"], "answer": res.get("answer"), "error": res.get("error"),
                     "stop_reason": res.get("stop_reason"), "trace": res.get("trace", []), "tool_calls": res.get("tool_calls"),
                     "model_calls": res.get("model_calls"), "ms": res.get("ms"), **g})
        if log:
            ans = (res.get("answer") or res.get("error") or "").replace("\n", " ")
            log(f"[{'OK' if g['behavior_ok'] and not g['flags'] else 'NG'}] {q['id']:<3} {q['expect']:<12} {tools}\n      답: {ans[:110]}"
                + (f"\n      표시: {', '.join(g['flags'])}" if g["flags"] else ""))
    flags = Counter(f.split(":")[0] for r in rows for f in r["flags"])
    summary = {"n": len(rows), "behavior_ok": sum(r["behavior_ok"] for r in rows), "flags": dict(flags),
               "all_clean": sum(1 for r in rows if r["behavior_ok"] and not r["flags"]),
               "by_expect": {e: [sum(r["behavior_ok"] for r in rows if r["expect"] == e), sum(1 for r in rows if r["expect"] == e)]
                             for e in sorted({r["expect"] for r in rows})}}
    return {"kind": "heldout_run", "created": datetime.now().isoformat(timespec="seconds"), "today": data["today"], "frozen_on": data["frozen_on"],
            "rows": rows, "summary": summary}


def rescore(path: str | Path, log=print) -> dict:
    """저장된 실행 기록(JSON)을 지금의 채점 기준으로 다시 채점한다 (수정 전 기록을 같은 기준으로 비교하려고)."""
    old = json.loads(Path(path).read_text(encoding="utf-8"))
    data = load()
    by_id = {q["id"]: q for q in data["questions"]}
    rows = []
    for r in old["rows"]:
        q = by_id[r["id"]]
        g = grade(q, {"answer": r.get("answer"), "error": r.get("error"), "trace": r.get("trace") or [], "today": data["today"]})
        rows.append({"id": r["id"], "question": q["question"], "expect": q["expect"], "answer": r.get("answer"), "error": r.get("error"),
                     "trace": r.get("trace") or [], **g})
        if log:
            log(f"[{'OK' if g['behavior_ok'] and not g['flags'] else 'NG'}] {r['id']:<3} {q['expect']:<12} {', '.join(g['flags']) or '표시 없음'}")
    flags = Counter(f.split(":")[0] for r in rows for f in r["flags"])
    summary = {"n": len(rows), "behavior_ok": sum(r["behavior_ok"] for r in rows), "flags": dict(flags),
               "all_clean": sum(1 for r in rows if r["behavior_ok"] and not r["flags"]),
               "by_expect": {e: [sum(r["behavior_ok"] for r in rows if r["expect"] == e), sum(1 for r in rows if r["expect"] == e)]
                             for e in sorted({r["expect"] for r in rows})}}
    return {"kind": "heldout_rescore", "source": str(path), "created": datetime.now().isoformat(timespec="seconds"), "today": data["today"],
            "rows": rows, "summary": summary}


def print_summary(res: dict, log=print) -> None:
    s = res["summary"]
    log(f"\n검증용 {s['n']}문항: 기대한 행동 {s['behavior_ok']}/{s['n']}, 표시 없이 깨끗한 것 {s['all_clean']}/{s['n']}  (깨끗한 것 = 기대한 행동 + 아래 표시가 하나도 없음)")
    log("  기대별: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in s["by_expect"].items()))
    log("  표시: " + (", ".join(f"{k} {v}" for k, v in s["flags"].items()) or "없음"))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="검증용 문항을 실제 모델로 돌려 행동을 확인한다")
    ap.add_argument("--save")
    ap.add_argument("--only")
    ap.add_argument("--rescore", metavar="RUN_JSON", help="저장된 실행 기록을 지금의 채점 기준으로 다시 채점 (모델을 부르지 않음)")
    a = ap.parse_args(argv)
    only = {s.strip() for s in a.only.split(",")} if a.only else None
    res = rescore(a.rescore) if a.rescore else run(only=only)
    print_summary(res)
    if a.save:
        Path(a.save).parent.mkdir(parents=True, exist_ok=True)
        Path(a.save).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\n저장: {a.save}")


if __name__ == "__main__":
    main()
