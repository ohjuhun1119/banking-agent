"""평가 실행 (10일차): eval/questions.jsonl의 문항을 실제 에이전트(Ollama)에 넣고 채점해 분류별로 요약한다.

실행 (레포 루트, Ollama 필요)
  python eval/run_eval.py --core --save eval/runs/eval_core_1.json     대표 문항(core)만 (기존 챗봇과 비교할 때 쓰는 묶음)
  python eval/run_eval.py --save eval/runs/eval_dev_1.json             개발용 전체
  python eval/run_eval.py --category missing --only Q065,Q066          일부만
  python eval/run_eval.py --sample 10 --seed 1                         무작위 10문항 (빠른 점검)
  python eval/run_eval.py --rescore eval/runs/eval_dev_1.json          저장된 결과를 모델 없이 다시 채점 (채점기를 고친 뒤)
  python eval/run_eval.py --retrieval [--k 5] [--mode ...]             검색 품질만 따로 (모델 없이, 색인 필요): Recall@k · MRR · 함정 청크
  python eval/run_eval.py --split held_out --save ...                  검증용: 고정이 깨지지 않았을 때만, 마일스톤마다 한 번만 (eval/heldout_tools.py 참고)
점수의 정의는 eval/evaluate.py 맨 위를 본다. 저장할 때 답변은 앞 300자만 남긴다 (약관 문장을 길게 옮기지 않으려고, --full-answers로 바꿀 수 있다).
저장 파일을 커밋하기 전에 답변에 약관 본문이 길게 인용돼 있지 않은지 확인한다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import heldout_tools as ht  # noqa: E402
from eval.evaluate import grade, retrieval_metrics, summarize  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.jsonl"
ANSWER_CHARS = 300
RES_KEYS = ("answer", "error", "stop_reason", "tool_calls", "model_calls", "ms", "trace", "evidence", "evidence_text")
HEADERS = ("n", "행동", "clean", "도구", "도구(모델만)", "계산값", "인자", "근거", "정답청크", "근거없음", "강제", "p50ms", "p90ms", "모델호출")


def load(path=None) -> list[dict]:
    return [json.loads(line) for line in Path(path or QUESTIONS).read_text(encoding="utf-8").splitlines() if line.strip()]


def select(questions: list[dict], split: str = "dev", core: bool = False, category: str | None = None, only: set[str] | None = None, limit: int | None = None,
           sample: int | None = None, seed: int = 0) -> list[dict]:
    """실행할 문항을 고른다 (순서는 파일 순서, sample은 고른 뒤에도 파일 순서를 지킨다)."""
    out = [q for q in questions if split == "all" or q.get("split") == split]
    if core:
        out = [q for q in out if q.get("core")]
    if category:
        out = [q for q in out if q["category"] == category]
    if only:
        out = [q for q in out if q["id"] in only]
    if sample is not None and sample < len(out):
        keep = set(random.Random(seed).sample(range(len(out)), sample))
        out = [q for i, q in enumerate(out) if i in keep]
    return out[:limit] if limit else out


def compact(res: dict, full: bool = False) -> dict:
    """저장할 결과: 필요한 칸만, 답변은 앞부분만."""
    out = {k: res.get(k) for k in RES_KEYS}
    if not full and isinstance(out["answer"], str):
        out["answer"] = out["answer"][:ANSWER_CHARS]
    return out


def run(questions: list[dict], agent=None, log=print, full: bool = False, progress=None) -> list[dict]:
    """문항마다 새 대화로 에이전트를 실행하고 채점한다. 한 문항이 실패해도 나머지는 계속한다.
    progress(i, n, 문항)는 각 문항을 **시작하기 전에** 불린다: 한 문항에서 오래 걸릴 때 멈춘 건지 느린 건지 화면에서 알 수 있다 (기본은 stderr에 한 줄)."""
    if agent is None:
        from app.graph import Agent
        agent = Agent()
    if progress is None:
        def progress(i, n, q):
            print(f"  [{i}/{n}] {q['id']} 실행 중 … ({time.strftime('%H:%M:%S')})", file=sys.stderr, flush=True)
    rows = []
    for i, q in enumerate(questions, 1):
        progress(i, len(questions), q)
        t0 = time.time()
        try:
            res = agent.ask(q["question"], f"eval-{q['id']}", today=q["today"])
        except Exception as e:                                         # 모델 · 서버 문제: 이 문항만 실패로 기록
            res = {"answer": None, "error": f"{type(e).__name__}: {e}", "stop_reason": "crash", "trace": [], "evidence": [], "ms": round((time.time() - t0) * 1000)}
        g = grade(q, res)
        rows.append({"id": q["id"], "category": q["category"], "core": q.get("core", False), "question": q["question"], "res": compact(res, full), "grade": g})
        tools = " ".join(f"{t['name']}→{t['status']}" + ("*" if t.get("forced") else "") for t in res.get("trace") or []) or "(도구 없음)"
        flags = f"  표시: {', '.join(g['flags'])}" if g["flags"] else ""
        log(f"[{'OK' if g['clean'] else 'NG'}] {q['id']} {q['category']:<14} {q['gold']['behavior']:<14} {tools}{flags}  ({time.time() - t0:.0f}초)\n      답: {(res.get('answer') or res.get('error') or '')[:100]}")
    return rows


def fmt_ratio(block: dict, key: str) -> str:
    v = block.get(key)
    return f"{v['hit']}/{v['n']}" if v else "-"


def table_rows(summary: dict) -> list[list[str]]:
    rows = []
    for name, b in [("전체", summary["overall"])] + sorted(summary["by_category"].items()):
        rows.append([name, str(b["n"]), fmt_ratio(b, "behavior_ok"), fmt_ratio(b, "clean"), fmt_ratio(b, "tools_ok"), fmt_ratio(b, "tools_model_only"), fmt_ratio(b, "calc_value_ok"),
                     fmt_ratio(b, "args_ok"), fmt_ratio(b, "evidence_ok"), fmt_ratio(b, "gold_chunk_in_evidence"), fmt_ratio(b, "unsupported"), fmt_ratio(b, "forced"),
                     "-" if b["ms_p50"] is None else str(round(b["ms_p50"])), "-" if b["ms_p90"] is None else str(round(b["ms_p90"])),
                     "-" if b["model_calls_avg"] is None else str(b["model_calls_avg"])])
    return rows


def format_summary(summary: dict, markdown: bool = False) -> str:
    head = ["분류", *HEADERS]
    rows = table_rows(summary)
    if markdown:
        lines = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"] + ["| " + " | ".join(r) + " |" for r in rows]
    else:
        w = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head)]
        lines = ["  ".join(h.ljust(w[i]) for i, h in enumerate(head))] + ["  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows]
    flags = summary["overall"]["flags"]
    lines.append("")
    lines.append("표시(flags): " + (", ".join(f"{k} {v}" for k, v in flags.items()) if flags else "없음"))
    lines.append("용어: 행동=기대한 행동, clean=행동이 맞고 표시(flags)가 하나도 없음, 도구(모델만)=코드의 강제 호출 없이 모델이 스스로 고름, 강제=코드가 대신 부른 문항, 근거없음=근거 없는 답변 표시가 있는 문항")
    return "\n".join(lines)


def rescore(path: str, questions: list[dict]) -> tuple[list[dict], dict]:
    """저장된 결과를 현재 문항 · 채점기로 다시 채점한다 (모델을 부르지 않는다). 문항 글이 바뀐 줄은 건너뛰고 알려 준다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in questions}
    rows, skipped = [], []
    for r in data["rows"]:
        q = by_id.get(r["id"])
        if q is None or " ".join(q["question"].split()) != " ".join(r["question"].split()):
            skipped.append(r["id"])
            continue
        rows.append({**r, "category": q["category"], "grade": grade(q, r["res"])})
    return rows, {"skipped": skipped, "meta": data.get("meta", {})}


def save(path: str, rows: list[dict], summary: dict, meta: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, agent=None, search_fn=None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 평가 실행 · 채점 · 요약")
    ap.add_argument("--split", choices=("dev", "held_out", "all"), default="dev")
    ap.add_argument("--core", action="store_true", help="대표 문항(core)만")
    ap.add_argument("--category")
    ap.add_argument("--only", help="쉼표로 구분한 문항 id")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save")
    ap.add_argument("--rescore")
    ap.add_argument("--retrieval", action="store_true")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--md", action="store_true", help="요약을 마크다운 표로")
    ap.add_argument("--full-answers", action="store_true")
    ap.add_argument("--allow-repeat", action="store_true", help="검증용을 같은 마일스톤에 다시 실행 (기록에 남는다)")
    a = ap.parse_args(argv)
    questions = load()
    chosen = select(questions, a.split, a.core, a.category, set(a.only.split(",")) if a.only else None, a.limit, a.sample, a.seed)
    if not chosen and not a.rescore:
        print("선택된 문항이 없어요." + (" (검증용 문항은 eval/heldout_tools.py로 받아 정답을 쓰고 고정해야 해요.)" if a.split == "held_out" else ""))
        return 1
    if a.retrieval:
        if search_fn is None:
            sys.path.insert(0, str(ROOT / "rag"))
            from search import search_terms as search_fn
        m = retrieval_metrics(chosen, search_fn, a.k)
        print(f"검색 품질 (질문 그대로 검색, 정답 청크가 있는 {m['n']}문항, 상위 {m['k']}개)")
        print(f"  Recall@{m['k']}: {m['recall_at_k']:.3f}   Recall@1: {m['recall_at_1']:.3f}   MRR: {m['mrr']:.3f}   정답보다 앞선 함정 청크가 있는 문항: {m['trap_before_gold']}")
        print("  못 찾은 문항:", ", ".join(m["missed"]) or "없음")
        return 0
    if a.rescore:
        rows, info = rescore(a.rescore, questions)
        print(f"다시 채점: {len(rows)}문항" + (f" (문항 글이 바뀌어 건너뜀: {', '.join(info['skipped'])})" if info["skipped"] else ""))
        print(format_summary(summarize(rows), a.md))
        return 0
    held = any(q.get("split") == "held_out" for q in chosen)
    if held:                                                          # 검증용 규칙: 고정이 깨지지 않았고, 마일스톤마다 한 번만
        bad = ht.verify()
        if bad:
            print("검증용 문항을 실행할 수 없어요:\n  " + "\n  ".join(bad))
            return 2
        if ht.runs() and not a.allow_repeat:
            print(f"검증용은 마일스톤마다 한 번만 실행해요. 이미 {len(ht.runs())}회 실행했어요 ({ht.runs()[-1]['date']}). 다시 하려면 --allow-repeat (기록에 남아요).")
            return 2
        if not a.save:
            print("검증용은 결과를 반드시 --save로 저장해야 해요 (한 번뿐인 측정이라 잃어버리면 안 돼요).")
            return 2
    rows = run(chosen, agent, full=a.full_answers)
    summary = summarize(rows)
    print()
    print(format_summary(summary, a.md))
    if a.save:
        save(a.save, rows, summary, {"split": a.split, "n": len(rows), "core": a.core, "date": time.strftime("%Y-%m-%d")})
        print(f"\n저장: {a.save}")
        if held:
            ht.record_run(a.save, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
