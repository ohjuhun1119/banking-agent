"""5일차: 모델이 질문마다 올바른 도구를 올바른 값으로 부르는지 확인하고, 도구 설명 문구 3종을 비교한다.

실행 (레포 루트, Ollama가 켜져 있어야 한다)
  python eval/tool_check.py --variant detailed --save eval/tool_runs/detailed.json      (short · examples도 같은 방식)
  python eval/tool_check.py --compare eval/tool_runs/short.json eval/tool_runs/detailed.json eval/tool_runs/examples.json
  python eval/tool_check.py --retrieval --variant detailed --save eval/tool_runs/query_detailed.json
        (검색어 재측정: 질문 그대로 검색 vs 모델이 만든 검색어로 검색. 먼저 python rag/ingest.py)
  보조: --repeat 3 (같은 질문을 여러 번: 흔들림 확인), --only C1,C3 (일부 질문만)

채점은 계산 도구가 실제로 받아들이는 방식(app/validators.py)으로 값을 읽어 비교한다.
오류 종류
  도구 선택  missing_call(불러야 할 도구를 안 부름) / extra_call(부르면 안 되는 도구를 부름) /
             unknown_tool(없는 도구 이름) / invalid_call(인자를 JSON으로 못 읽음) / empty_query(검색어가 비어 있음)
  인자       extra_arg(질문에 없는 값을 넣음: 지어냄) / missing_arg(질문에 있는 값을 빠뜨림) / wrong_<인자>(값이 다름) /
             date_not_iso(날짜가 YYYY-MM-DD가 아님: 일수 · 한글 날짜) / unparsable_principal(예: '1000만원')
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from app import tool_calling as tc  # noqa: E402
from app.validators import parse_date, parse_principal, parse_rate  # noqa: E402
import tool_cases  # noqa: E402

SELECTION_ERRORS = {"missing_call", "extra_call", "unknown_tool", "invalid_call", "empty_query"}
PARSERS = {"principal": parse_principal, "start_date": parse_date, "maturity_date": parse_date,
           "termination_date": parse_date, "base_rate": parse_rate}
PARSE_ERROR = {"principal": "unparsable_principal", "start_date": "date_not_iso", "maturity_date": "date_not_iso",
               "termination_date": "date_not_iso", "base_rate": "wrong_base_rate"}


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _err(code: str, field: str | None = None, detail=None) -> dict:
    e = {"code": code}
    if field:
        e["field"] = field
    if detail is not None:
        e["detail"] = detail
    return e


def _check_arg(field: str, got, want) -> dict | None:
    """모델이 넣은 값(got)을 도구와 같은 방식으로 읽어 기대값(want)과 비교한다. 같으면 None."""
    try:
        g = PARSERS[field](got)
    except ValueError:
        return _err(PARSE_ERROR[field], field, got)
    if g != PARSERS[field](want):
        return _err(f"wrong_{field}", field, {"got": got, "want": want})
    return None


def _check_calc_args(args: dict, expected: dict, strict: bool) -> list[dict]:
    errors = []
    for field, got in args.items():
        if _blank(got):
            continue                                         # 비워 둔 값은 넣지 않은 것으로 본다
        if field not in expected:
            errors.append(_err("extra_arg", field, got))     # 질문에 없는 값(또는 없는 인자 이름)을 넣었다
            continue
        e = _check_arg(field, got, expected[field])
        if e:
            errors.append(e)
    if strict:
        for field in expected:
            if _blank(args.get(field)):
                errors.append(_err("missing_arg", field))
    return errors


def grade(case: dict, out: dict) -> dict:
    """한 질문의 모델 출력을 채점한다. 반환: {"pass", "errors", "notes"}"""
    exp = case["expect"]
    calls, errors, notes = out.get("tool_calls", []), [], []
    for bad in out.get("invalid_calls", []):
        errors.append(_err("invalid_call", None, bad.get("name")))
    for c in calls:
        if c["name"] not in tc.TOOL_NAMES:
            errors.append(_err("unknown_tool", None, c["name"]))
    search = [c for c in calls if c["name"] == "search_terms"]
    calc = [c for c in calls if c["name"] == "calc_interest"]
    for c in search:
        if _blank(c["args"].get("query")):
            errors.append(_err("empty_query"))

    if exp["tool"] is None:
        for c in calls:
            if c["name"] in tc.TOOL_NAMES:
                errors.append(_err("extra_call", None, c["name"]))
    elif exp["tool"] == "search_terms":
        if not search:
            errors.append(_err("missing_call", None, "search_terms"))
        if calc:
            errors.append(_err("extra_call", None, "calc_interest"))
    else:                                                    # calc_interest
        if search:
            notes.append("검색 도구도 함께 부름 (허용)")
        if len(calc) > 1:
            notes.append(f"계산 도구를 {len(calc)}번 부름 (첫 호출만 채점)")
        if exp["strict"]:
            if not calc:
                errors.append(_err("missing_call", None, "calc_interest"))
            else:
                errors += _check_calc_args(calc[0]["args"], exp["args"], strict=True)
        elif calc:
            errors += _check_calc_args(calc[0]["args"], exp.get("allowed", {}), strict=False)
        else:
            notes.append("도구 없이 되물음 (허용)")
    return {"pass": not errors, "errors": errors, "notes": notes}


def _select_cases(only: str | None) -> list[dict]:
    if not only:
        return tool_cases.CASES
    ids = [s.strip() for s in only.split(",") if s.strip()]
    known = {c["id"]: c for c in tool_cases.CASES}
    missing = [i for i in ids if i not in known]
    if missing:
        raise SystemExit(f"없는 질문 id: {', '.join(missing)}")
    return [known[i] for i in ids]


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    sel_ok = [r for r in rows if not any(e["code"] in SELECTION_ERRORS for e in r["errors"])]
    groups: dict[str, list[int]] = {}
    for r in rows:
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += r["pass"]
        g[1] += 1
    codes = Counter(e["code"] for r in rows for e in r["errors"])
    ms = [r["ms"] for r in rows if r.get("ms") is not None]
    flaky = []
    by_id: dict[str, set] = {}
    for r in rows:
        by_id.setdefault(r["id"], set()).add(r["pass"])
    flaky = sorted(i for i, s in by_id.items() if len(s) > 1)
    return {"total": total, "pass": sum(r["pass"] for r in rows), "selection_ok": len(sel_ok),
            "args_ok": sum(r["pass"] for r in sel_ok), "by_group": {k: f"{v[0]}/{v[1]}" for k, v in groups.items()},
            "error_codes": dict(codes.most_common()), "median_ms": int(statistics.median(ms)) if ms else None,
            "flaky": flaky}


def run(variant: str, cases: list[dict] | None = None, model=None, repeat: int = 1, today: str = tool_cases.TODAY,
        log=print) -> dict:
    cases = cases or tool_cases.CASES
    model = model or tc.get_model(variant)
    rows = []
    for rep in range(repeat):
        for case in cases:
            out = tc.ask(case["question"], variant, today, model=model)
            g = grade(case, out)
            row = {"id": case["id"], "group": case["group"], "question": case["question"], "repeat": rep + 1,
                   "calls": out["tool_calls"], "invalid_calls": out["invalid_calls"], "content": out["content"][:300],
                   "ms": out["ms"], **g}
            rows.append(row)
            if log:
                mark = "통과" if g["pass"] else "실패"
                calls = "; ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)})" for c in out["tool_calls"]) or "(도구 없음)"
                errs = ", ".join(e["code"] + (f"[{e['field']}]" if e.get("field") else "") for e in g["errors"])
                log(f"[{mark}] {case['id']:<3} {calls}" + (f"  <- {errs}" if errs else "") + f"  {out['ms']}ms")
    return {"kind": "tools", "variant": variant, "model": tc.TOOL_MODEL, "today": today, "repeat": repeat,
            "created": datetime.now().isoformat(timespec="seconds"), "rows": rows, "summary": summarize(rows)}


def print_summary(res: dict, log=print) -> None:
    s = res["summary"]
    log(f"\n설명 문구 {res['variant']} · 모델 {res['model']} · 오늘 {res['today']} · 반복 {res['repeat']}회")
    log(f"통과 {s['pass']}/{s['total']}  도구 선택 {s['selection_ok']}/{s['total']}  "
        f"인자(도구 선택이 맞은 것 중) {s['args_ok']}/{s['selection_ok']}  응답 시간 중앙값 {s['median_ms']}ms")
    log("그룹별: " + ", ".join(f"{k} {v}" for k, v in s["by_group"].items()))
    if s["error_codes"]:
        log("오류 종류: " + ", ".join(f"{k} {v}" for k, v in s["error_codes"].items()))
    if s["flaky"]:
        log("반복마다 결과가 달라진 질문: " + ", ".join(s["flaky"]))


def compare(paths: list[str], log=print) -> dict:
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    log(f"{'설명 문구':<10} {'통과':>7} {'도구 선택':>9} {'인자':>7} {'중앙값':>8}  주요 오류")
    for r in runs:
        s = r["summary"]
        top = ", ".join(f"{k} {v}" for k, v in list(s["error_codes"].items())[:3])
        log(f"{r['variant']:<10} {s['pass']:>3}/{s['total']:<3} {s['selection_ok']:>4}/{s['total']:<4} "
            f"{s['args_ok']:>3}/{s['selection_ok']:<3} {str(s['median_ms']) + 'ms':>8}  {top}")
    # 질문별로 결과가 달라진 것만 보여준다 (반복 실행이면 통과 횟수)
    table: dict[str, list[str]] = {}
    for i, r in enumerate(runs):
        for row in r["rows"]:
            cell = table.setdefault(row["id"], ["-"] * len(runs))
            prev = cell[i]
            p = 1 if row["pass"] else 0
            cell[i] = str(p) if prev == "-" else str(int(prev) + p)
    diff = {k: v for k, v in table.items() if len(set(v)) > 1}
    if diff:
        log("\n문구에 따라 결과가 달라진 질문 (숫자 = 통과 횟수): " + " / ".join(r["variant"] for r in runs))
        for k, v in diff.items():
            q = next(row["question"] for row in runs[0]["rows"] if row["id"] == k) if runs[0]["rows"] else ""
            log(f"  {k:<3} {'  '.join(v)}  {q}")
    else:
        log("\n세 문구 모두 질문별 결과가 같습니다.")
    return {"variants": [r["variant"] for r in runs], "diff": diff}


# ---------- 검색어 재측정 ----------
def _rank(results: list[dict], answers: set[str]) -> int | None:
    return next((r["rank"] for r in results if r["chunk_id"] in answers), None)


def _retrieval_stats(ranks: list[int | None]) -> dict:
    n = len(ranks)
    return {"n": n, "top1": sum(1 for r in ranks if r == 1), "top3": sum(1 for r in ranks if r and r <= 3),
            "top5": sum(1 for r in ranks if r and r <= 5), "mrr": round(sum(1 / r for r in ranks if r) / n, 3) if n else 0}


def run_retrieval(variant: str, model=None, search_fn=None, k: int = 5, today: str = tool_cases.TODAY, log=print) -> dict:
    """check_search의 정답 있는 질문으로, 질문 그대로 검색한 순위와 모델이 만든 검색어로 검색한 순위를 비교한다.
    모델이 검색 도구를 부르지 않으면 실패로 센다."""
    sys.path.insert(0, str(ROOT / "rag"))
    import check_search
    if search_fn is None:
        from search import search_terms as search_fn
    model = model or tc.get_model(variant)
    rows = []
    for group, question, answers, _avoid in check_search.QUESTIONS:
        if not answers:
            continue
        base = search_fn(question, product=tc.PRODUCT, k=k, max_distance=None)
        if base.get("status") == "error":
            raise SystemExit(base["message"])
        out = tc.ask(question, variant, today, model=model)
        q = next((c["args"].get("query") for c in out["tool_calls"] if c["name"] == "search_terms"), None)
        if q and isinstance(q, str) and q.strip():
            mres = search_fn(q.strip(), product=tc.PRODUCT, k=k, max_distance=None)
            mrank = _rank(mres.get("results", []), answers)
        else:
            mrank = None
        brank = _rank(base.get("results", []), answers)
        rows.append({"group": group, "question": question, "model_query": q,
                     "other_calls": [c["name"] for c in out["tool_calls"] if c["name"] != "search_terms"],
                     "base_rank": brank, "model_rank": mrank, "ms": out["ms"]})
        if log:
            log(f"{(str(brank) if brank else '-'):>2} -> {(str(mrank) if mrank else '-'):>2}  {question}  =>  {q or '(검색 안 함)'}")
    summary = {"question": _retrieval_stats([r["base_rank"] for r in rows]),
               "model_query": _retrieval_stats([r["model_rank"] for r in rows]),
               "no_search": sum(1 for r in rows if not r["model_query"])}
    return {"kind": "retrieval", "variant": variant, "model": tc.TOOL_MODEL, "today": today, "k": k,
            "created": datetime.now().isoformat(timespec="seconds"), "rows": rows, "summary": summary}


def print_retrieval(res: dict, log=print) -> None:
    s = res["summary"]
    log(f"\n검색어 재측정 (설명 문구 {res['variant']}, 상위 {res['k']}개, 정답 있는 {s['question']['n']}문항)")
    for name, key in (("질문 그대로", "question"), ("모델 검색어", "model_query")):
        st = s[key]
        log(f"  {name:<6} 1위 {st['top1']}/{st['n']}  3위 안 {st['top3']}/{st['n']}  5위 안 {st['top5']}/{st['n']}  MRR {st['mrr']}")
    log(f"  모델이 검색 도구를 부르지 않은 질문: {s['no_search']}개 (실패로 셈)")


def _save(res: dict, path: str | None, log=print) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(f"\n저장: {p}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="도구 선택 확인 · 설명 문구 비교 · 검색어 재측정")
    ap.add_argument("--variant", choices=tc.VARIANTS, default=tc.DEFAULT_VARIANT)
    ap.add_argument("--save")
    ap.add_argument("--compare", nargs="+", metavar="RUN_JSON")
    ap.add_argument("--retrieval", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only")
    ap.add_argument("--today", default=tool_cases.TODAY)
    a = ap.parse_args(argv)
    try:
        if a.compare:
            compare(a.compare)
            return
        if a.retrieval:
            res = run_retrieval(a.variant, today=a.today)
            print_retrieval(res)
        else:
            res = run(a.variant, _select_cases(a.only), repeat=max(1, a.repeat), today=a.today)
            print_summary(res)
        _save(res, a.save)
    except tc.ToolCallingError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
