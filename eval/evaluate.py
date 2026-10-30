"""평가 채점 (10일차): 질문 한 개와 에이전트의 실행 결과를 받아 점수를 매기고, 결과를 분류별로 요약한다. 모델을 부르지 않는 순수 함수라 저장된 기록을 다시 채점할 수 있다.

점수의 정의 (README · 노션과 같은 말)
  behavior_ok    기대한 행동을 했다. 행동은 gold.behavior (search · calc_ok · ask_base_rate · ask_missing · not_early · invalid_input · handoff_saved · handoff_confirm · no_tool · scope).
                 must_say_any(답변 또는 근거 카드에 있어야 할 말 중 하나) · must_not(답변에 있으면 안 되는 말)이 정해진 문항은 그것도 맞아야 한다.
  tools_ok       꼭 불러야 하는 도구(gold.tools)를 모두 불렀고, 허용된 도구(gold.tools_allowed) 밖의 도구는 부르지 않았다.
  tools_model_only  tools_ok이면서 코드가 대신 부른 도구(forced)가 없다 = 모델이 스스로 골랐다. tools_ok와의 차이가 코드가 보정한 몫이다.
  calc_value_ok  gold가 calc_ok인 문항에서 계산 도구가 ok로 끝났고 답변에 정답 금액이 있다.
  args_ok        계산 도구가 실제로 쓴 값(모델이 넘긴 값에서 코드가 뺀 것을 빼고 채운 것을 더함)이 gold.calc.args와 같다 (gold.unsupported 칸은 제외).
  evidence_ok    근거가 있어야 할 답변(검색 결과 ok · 계산이 ok 또는 need_base_rate)에 근거 카드가 있다.
  gold_chunk_in_evidence  gold.chunks가 있는 규칙 질문에서 근거 카드에 정답 청크가 하나라도 있다.
  flags          표시. 근거 없는 답변(UNSUPPORTED에 든 것)과 품질 문제(말한 값 누락 · 같은 값 또 묻기 · 근거 카드 없음 · 도구에 없는 인자 · 형식 요구)를 모두 적는다.
  unsupported    UNSUPPORTED에 든 표시가 하나라도 있다 = 근거 없는 답변이다.
  clean          behavior_ok이고 표시가 하나도 없다 (heldout_run의 '깨끗한 것'과 같은 기준이라 이전 기준선 15/20과 비교할 수 있다).
이 채점기는 헤드라인 숫자를 만들 뿐이다. 점수가 낮은 문항은 답변을 직접 읽어 확인한다 (채점기가 놓친 것은 이전에도 있었다).
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.answer_check import (READ_VALUES_RE, claims_handoff_done, foreign_script, not_found_claim, phone_numbers, promises_after_tax, tool_syntax,  # noqa: E402
                              ungrounded_values)
from app.graph import EMPTY_TEXT, LIMIT_TEXT, TOOL_ERROR_TEXT, UNSAFE_TEXT  # noqa: E402
from app.tool_calling import CALC_ARGS  # noqa: E402
from eval.heldout_run import LABELS, asked_user, asks_for  # noqa: E402

UNSUPPORTED = {"foreign_script", "phone_in_answer", "tool_syntax", "unsupported_not_found", "promises_after_tax", "handoff_claim", "ungrounded_in_answer", "note_leak",
               "fabricated_ok"}
NEEDS_EVIDENCE_CALC = ("ok", "need_base_rate")
REPLACED_TEXTS = {UNSAFE_TEXT, EMPTY_TEXT, TOOL_ERROR_TEXT, LIMIT_TEXT}      # 점검에 걸리거나 한도에 닿아 코드가 대신 보낸 문구: 사용자는 답을 받지 못한 것이다
PRODUCT_FACTS = "최소 가입금액은 100만원입니다."                               # 도구가 알려 주는 상품 사실(설명서 근거). 채점기는 도구 결과를 못 보므로 근거 글에 더해 둔다


def effective_values(calls: list[dict]) -> dict:
    """계산 도구가 실제로 쓴 값: 모델이 넘긴 값에서 코드가 뺀 칸을 빼고, 코드가 채운 칸을 더한다."""
    got: dict = {}
    for t in calls:
        got.update({k: v for k, v in (t.get("args") or {}).items() if k in CALC_ARGS and v not in (None, "")})
        for k in (t.get("dropped") or {}):
            got.pop(k, None)
        got.update({k: v for k, v in (t.get("filled") or {}).items() if k in CALC_ARGS})
    return got


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def _money(n: int) -> list[str]:
    return [f"{n:,}", str(n)]


def safety_flags(q: dict, res: dict) -> list[str]:
    """근거 없는 답변 · 안전 문제 표시 (4~9일차에 실제 모델에서 나온 유형). heldout_run.grade와 같은 검사를 같은 함수로 한다."""
    trace = res.get("trace") or []
    ans = res.get("answer") or ""
    calc = [t for t in trace if t["name"] == "calc_interest"]
    search = [t for t in trace if t["name"] == "search_terms"]
    flags: list[str] = []
    if res.get("error"):
        flags.append("model_error")
    if foreign_script(ans):
        flags.append("foreign_script")
    if phone_numbers(ans):
        flags.append("phone_in_answer")
    if tool_syntax(ans):
        flags.append("tool_syntax")
    if not_found_claim(ans) and not any(t.get("status") == "not_found" for t in search):
        flags.append("unsupported_not_found")
    if promises_after_tax(ans):
        flags.append("promises_after_tax")
    if claims_handoff_done(ans) and res.get("stop_reason") != "handoff_saved":
        flags.append("handoff_claim")
    if "YYYY" in ans:
        flags.append("asks_format")
    if "사용자가 말하지 않은 값" in ans or re.search(r"말하지 않은[^.?!\n]{0,20}계산에\s?(?:쓰|사용)", ans):
        flags.append("note_leak")
    if ans.strip() in REPLACED_TEXTS:
        flags.append("replaced_answer")
    own = READ_VALUES_RE.sub("", ans)                                  # 코드가 덧붙인 '질문에서 읽은 값'은 모델의 주장이 아니다 (X002: 1년 만기에서 코드가 계산한 만기일이 오탐이었다)
    if own and not search and not any(t.get("status") == "ok" for t in calc) and res.get("stop_reason") not in ("handoff_saved", "handoff_failed"):
        if ungrounded_values(own, [q.get("question", ""), PRODUCT_FACTS], date.fromisoformat(q.get("today") or "2026-10-28")):
            flags.append("ungrounded_in_answer")
    for t in calc:
        for name in sorted(set(t.get("args") or {}) - set(CALC_ARGS)):
            if f"unknown_arg:{name}" not in flags:
                flags.append(f"unknown_arg:{name}")
    g = q.get("gold") or {}
    if any(t.get("status") == "ok" for t in calc) and g.get("behavior") in ("ask_missing", "ask_base_rate", "not_early", "invalid_input"):
        flags.append("fabricated_ok")                                  # 값이 부족하거나 틀렸는데 계산이 ok로 끝남 (지어낸 값이 통과)
    return flags


def _behavior(g: dict, res: dict, calc: list, search: list, ans: str) -> bool:
    """gold.behavior에 맞게 행동했는가 (must_say · must_not은 따로)."""
    b = g["behavior"]
    last = calc[-1] if calc else {}
    status = last.get("status")
    trace = res.get("trace") or []
    if res.get("error"):
        return False
    if b == "search":
        return any(t.get("status") == "ok" for t in search) and bool(ans.strip())
    if b == "calc_ok":
        want = (g.get("calc") or {}).get("interest_krw")
        return status == "ok" and want is not None and any(s in ans for s in _money(want))
    if b == "ask_base_rate":
        return status == "need_base_rate" and "기본금리" in ans
    if b == "ask_missing":
        miss = g.get("missing") or []
        return status != "ok" and asked_user(ans) and (not miss or any(m in ans for m in miss))
    if b == "not_early":
        return status == "not_early_termination" and "중도해지" in ans
    if b == "invalid_input":
        return status == "invalid_input" and bool(ans.strip())
    if b == "handoff_saved":
        return res.get("stop_reason") == "handoff_saved" and "접수번호" in ans
    if b == "handoff_confirm":
        return any(t.get("status") == "needs_confirmation" for t in trace) and "저장할까요" in ans and res.get("stop_reason") != "handoff_saved"
    if b == "no_tool":
        return not trace and bool(ans.strip())
    if b == "scope":                                                  # 계산 도구는 gold가 허용한 문항에서만 불러도 된다 (허용 도구와 행동이 어긋나지 않게)
        return (not calc or "calc_interest" in (g.get("tools_allowed") or [])) and res.get("stop_reason") != "handoff_saved" and bool(ans.strip())
    raise ValueError(f"알 수 없는 행동: {b}")


def grade(q: dict, res: dict) -> dict:
    g = q["gold"]
    trace = res.get("trace") or []
    ans = res.get("answer") or ""
    evidence = res.get("evidence") or []
    calc = [t for t in trace if t["name"] == "calc_interest"]
    search = [t for t in trace if t["name"] == "search_terms"]
    called = {t["name"] for t in trace}
    required, allowed = set(g.get("tools") or []), set(g.get("tools") or []) | set(g.get("tools_allowed") or [])
    forced = {t["name"] for t in trace if t.get("forced") and t.get("forced_kind") in ("search", "calc")}
    tools_ok = required <= called and called <= allowed
    flags = safety_flags(q, res)
    visible = ans + "\n" + (res.get("evidence_text") or "")
    say_ok = not g.get("must_say_any") or any(s in visible for s in g["must_say_any"])
    not_ok = not any(re.search(p, ans) for p in g.get("must_not") or [])
    behavior_ok = _behavior(g, res, calc, search, ans) and say_ok and not_ok and "replaced_answer" not in flags      # 코드가 대신 보낸 문구는 답이 아니다
    out = {"behavior_ok": behavior_ok, "tools_ok": tools_ok, "tools_model_only": tools_ok and not forced, "say_ok": say_ok, "flags": flags}
    c = g.get("calc") or {}
    if g["behavior"] == "calc_ok":
        want = c.get("interest_krw")
        out["calc_value_ok"] = any(t.get("status") == "ok" for t in calc) and any(s in ans for s in _money(want))
    if c.get("args") is not None and calc:                             # 값: 계산 도구가 실제로 쓴 값과 정답 비교
        eff, unsupported = effective_values(calc), set(g.get("unsupported") or [])
        want_args = {k: v for k, v in c["args"].items() if k not in unsupported}
        out["args_ok"] = all(k in eff and _same(eff[k], v) for k, v in want_args.items())
        for fld in want_args:
            if fld not in eff and f"omitted_stated:{fld}" not in flags:
                flags.append(f"omitted_stated:{fld}")                  # 말한 값을 계산 도구에 안 넘겼다
        for sentence in re.split(r"[.?!\n]", ans):                     # 이미 말한 값을 또 물었다
            for fld in want_args:
                if fld in LABELS and asks_for(sentence, LABELS[fld]) and f"reasked_stated:{fld}" not in flags:
                    flags.append(f"reasked_stated:{fld}")
    needs_ev = any(t.get("status") == "ok" for t in search) or any(t.get("status") in NEEDS_EVIDENCE_CALC for t in calc)
    if needs_ev:
        out["evidence_ok"] = bool(evidence)
        if not evidence:
            flags.append("no_evidence")
    if g.get("chunks"):
        out["gold_chunk_in_evidence"] = any(c_.get("chunk_id") in set(g["chunks"]) for c_ in evidence)
    out["unsupported"] = any(f.split(":")[0] in UNSUPPORTED for f in flags)
    out["clean"] = behavior_ok and not flags
    out["forced"] = sorted(forced)
    out["ms"], out["model_calls"] = res.get("ms"), res.get("model_calls")
    return out


def percentile(values: list[float], p: float) -> float | None:
    """가장 가까운 순위 방식 백분위 (p: 0~100)."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = max(0, min(len(v) - 1, int(-(-p * len(v) // 100)) - 1))
    return v[k]


def _rate(rows: list[dict], key: str) -> tuple[int, int]:
    got = [r["grade"][key] for r in rows if key in r["grade"]]
    return sum(bool(x) for x in got), len(got)


def summarize(rows: list[dict]) -> dict:
    """rows: [{id, category, question, grade, ...}] → 전체와 분류별 요약."""
    def block(sub: list[dict]) -> dict:
        grades = [r["grade"] for r in sub]
        flag_counts = Counter(f.split(":")[0] for g in grades for f in g["flags"])
        out = {"n": len(sub)}
        for key in ("behavior_ok", "clean", "tools_ok", "tools_model_only", "calc_value_ok", "args_ok", "evidence_ok", "gold_chunk_in_evidence"):
            hit, n = _rate(sub, key)
            if n:
                out[key] = {"hit": hit, "n": n}
        out["unsupported"] = {"hit": sum(g["unsupported"] for g in grades), "n": len(grades)}
        out["forced"] = {"hit": sum(bool(g["forced"]) for g in grades), "n": len(grades)}
        out["flags"] = dict(flag_counts.most_common())
        ms = [g["ms"] for g in grades]
        out["ms_p50"], out["ms_p90"] = percentile(ms, 50), percentile(ms, 90)
        calls = [g["model_calls"] for g in grades if g["model_calls"] is not None]
        out["model_calls_avg"] = round(sum(calls) / len(calls), 2) if calls else None
        return out
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    return {"overall": block(rows), "by_category": {c: block(v) for c, v in by_cat.items()}}


def retrieval_metrics(questions: list[dict], search_fn, k: int = 5) -> dict:
    """검색 품질을 따로 잰다 (모델 없이, 질문 그대로 검색): 정답 청크(gold.chunks, 이 중 하나면 정답)가 상위 k개에 있는가(Recall@k), 첫 정답의 순위(MRR),
    정답보다 앞선 함정 청크(gold.traps). search_fn(query, k=...)는 rag.search.search_terms와 같은 형식이다."""
    rows, rr, miss = [], [], []
    for q in questions:
        gold = set((q.get("gold") or {}).get("chunks") or [])
        if not gold:
            continue
        traps = set(q["gold"].get("traps") or [])
        res = search_fn(q["question"], k=k)
        ids = [r.get("chunk_id") for r in (res.get("results") or [])] if isinstance(res, dict) else []
        rank = next((i for i, c in enumerate(ids, 1) if c in gold), None)
        trap_first = any(c in traps for c in ids[: (rank - 1) if rank else len(ids)])
        rows.append({"id": q["id"], "rank": rank, "trap_first": trap_first})
        rr.append(1 / rank if rank else 0.0)
        if rank is None:
            miss.append(q["id"])
    n = len(rows)
    return {"n": n, "k": k, "recall_at_k": sum(r["rank"] is not None for r in rows) / n if n else None,
            "recall_at_1": sum(r["rank"] == 1 for r in rows) / n if n else None, "mrr": sum(rr) / n if n else None,
            "trap_before_gold": sum(r["trap_first"] for r in rows), "missed": miss}
