"""기존 챗봇(https://github.com/ohjuhun1119/banking_chatbot)과 이번 에이전트를 같은 질문으로 비교 (12일차, 튜터 피드백 2번).

튜터 피드백: `같은 질문 30~50개를 기존 챗봇과 이번 버전에 넣고, 계산이 맞는지 · 근거가 정확한지 · 정보가 부족하면 다시 묻는지 · 응답 시간을 기록하고,
대화 중 원금이나 날짜를 바꾸는 사례도 포함`.

실행 (레포 루트. 이번 에이전트는 Ollama가 필요하고, 기존 챗봇 서버는 따로 띄운다)
  # 1) 기존 챗봇: 그 레포 폴더에서 (분류 모델 models/ 와 .env 가 있어야 한다. 포트는 우리 서버와 겹치지 않게 8001)
  #    python -m uvicorn app.main:app --port 8001
  # 2) 이 레포에서
  python eval/compare_chatbots.py --old-model cloud --save eval/runs/compare_cloud_1.json
  python eval/compare_chatbots.py --old-model local --skip-new --save eval/runs/compare_local_1.json      (로컬 모델로도 보고 싶을 때)
  python eval/compare_chatbots.py --rescore eval/runs/compare_cloud_1.json --md                           (저장된 결과를 다시 요약, 서버 없이)

비교 문항: 개발용 core 중 약관 질문 · 세후 · 계산 · 기본금리가 필요한 계산 · 정보 부족 5개 분류 39개 + 대화 중 값을 바꾸는 시나리오 6개(17턴, eval/compare_scenarios.json).
점수는 답변 **글만** 보고 두 시스템에 똑같이 매긴다 (정답 금액이 답에 있는지 · 값을 알려 달라고 되묻는지 · 약관 핵심 사실이 맞는지). 근거는 근거 카드가 있는지를 본다.
기존 챗봇은 정책상 구체적인 금리 · 금액을 말하지 않고 되묻지 않도록 설계되어 있어서(prompts/system_prompt.md, topic_policies.json), 계산과 되묻기에서 점수가 낮은 것은
성능 문제가 아니라 설계의 차이다. 그래서 점수와 함께 설계 차이를 같이 보고한다. 기존 챗봇은 질문 1개에 응답 1개라 시나리오의 턴을 따로 보낸다.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.evaluate import _money  # noqa: E402
from eval.heldout_run import asked_user  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.jsonl"
SCENARIOS = ROOT / "eval" / "compare_scenarios.json"
COMPARE_CATEGORIES = ("rule", "aftertax", "calc", "calc_base_rate", "missing")
SAVED_CHARS = 300                                   # 저장할 답변 길이 (약관 문장을 길게 옮기지 않으려고, run_eval과 같다)
SYSTEMS = ("old", "new")


# ---------- 문항
def load_questions(path: Path = QUESTIONS) -> list[dict]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["split"] == "dev" and r["core"] and r["category"] in COMPARE_CATEGORIES]


def load_scenarios(path: Path = SCENARIOS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------- 점수: 답변 글만 본다
def kind_of(gold: dict) -> str | None:
    """이 문항을 글만으로 채점할 수 있는 종류: calc(정답 금액) · ask(되묻기) · fact(약관 핵심 사실). 그 밖은 None."""
    behavior = gold["behavior"]
    if behavior == "calc_ok":
        return "calc"
    if behavior in ("ask_base_rate", "ask_missing"):
        return "ask"
    if gold.get("facts_all") or gold.get("facts_any") or gold.get("must_not"):
        return "fact"
    return None


def score_answer(gold: dict, answer: str) -> dict:
    kind, answer = kind_of(gold), answer or ""
    if kind == "calc":
        want = (gold.get("calc") or {}).get("interest_krw")
        correct = want is not None and any(s in answer for s in _money(want))
    elif kind == "ask":
        miss = gold.get("missing") or []
        if gold["behavior"] == "ask_base_rate":
            correct = asked_user(answer) and "기본금리" in answer
        else:
            correct = asked_user(answer) and (not miss or any(m in answer for m in miss))
    elif kind == "fact":
        all_, any_ = gold.get("facts_all") or [], gold.get("facts_any") or []
        correct = (all(re.search(p, answer) for p in all_) and (not any_ or any(re.search(p, answer) for p in any_))
                   and not any(re.search(p, answer) for p in gold.get("must_not") or []))
    else:
        return {"kind": None, "correct": None}
    return {"kind": kind, "correct": bool(correct)}


def score_expect(expect: dict, answer: str) -> bool:
    """시나리오의 한 턴: 정답 금액이 답에 있거나(interest_krw), 값을 알려 달라고 되묻는다(asks)."""
    answer = answer or ""
    if "interest_krw" in expect:
        return any(s in answer for s in _money(expect["interest_krw"]))
    return asked_user(answer) and any(label in answer for label in expect.get("asks") or [])


# ---------- 두 시스템을 부르는 함수
def ask_old(base_url: str, question: str, model: str = "cloud", timeout: float = 180.0) -> dict:
    """기존 챗봇 POST /chat {question, model}. 어떤 실패도 예외 대신 error 칸에 담는다."""
    body = json.dumps({"question": question, "model": model}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"answer": data.get("answer") or "", "route": data.get("route"), "label": data.get("label"), "error": data.get("error"),
                "ms": int((time.perf_counter() - start) * 1000), "evidence_shown": False}
    except urllib.error.HTTPError as e:
        return {"answer": "", "route": None, "label": None, "error": f"HTTP {e.code}", "ms": int((time.perf_counter() - start) * 1000), "evidence_shown": False}
    except Exception as e:                                                  # 연결 거부 · 시간 초과 · 잘못된 응답
        return {"answer": "", "route": None, "label": None, "error": f"{type(e).__name__}: {e}", "ms": int((time.perf_counter() - start) * 1000), "evidence_shown": False}


def old_alive(base_url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=timeout):
            return True
    except Exception:
        return False


def ask_new(agent, question: str, session: str, today: str) -> dict:
    start = time.perf_counter()
    res = agent.ask(question, session, today=today)
    return {"answer": res.get("answer") or "", "route": res.get("stop_reason"), "label": None, "error": res.get("error"),
            "ms": int((time.perf_counter() - start) * 1000), "evidence_shown": bool(res.get("evidence_text")),
            "model_calls": res.get("model_calls"), "tools": [t["name"] for t in res.get("trace") or []]}


# ---------- 실행
def _entry(raw: dict, score: dict | None = None, ok: bool | None = None) -> dict:
    out = {k: v for k, v in raw.items() if k != "answer"}
    out["answer"] = raw["answer"][:SAVED_CHARS]
    if score is not None:
        out.update(score)
    if ok is not None:
        out["correct"] = ok
    return out


def run(questions: list[dict], scenarios: dict, systems: dict, old_model: str = "cloud", progress=print) -> dict:
    """systems: {'old': 함수(question) -> dict, 'new': 함수(question, session) -> dict} (없는 시스템은 건너뜀)."""
    today = scenarios["today"]
    rows, total = [], len(questions)
    for i, q in enumerate(questions, 1):
        progress(f"  [{i}/{total}] {q['id']} 실행 중 …")
        row = {"id": q["id"], "category": q["category"], "behavior": q["gold"]["behavior"], "question": q["question"]}
        for name in SYSTEMS:
            if name not in systems:
                continue
            raw = systems[name](q["question"], f"cmp-{q['id']}") if name == "new" else systems[name](q["question"])
            row[name] = _entry(raw, score_answer(q["gold"], raw["answer"]))
        rows.append(row)
    scen_rows = []
    for s in scenarios["scenarios"]:
        progress(f"  [{s['id']}] {s['title']}")
        srow = {"id": s["id"], "title": s["title"], "turns": []}
        for n, turn in enumerate(s["turns"], 1):
            trow = {"turn": n, "question": turn["question"], "expect": turn["expect"]}
            for name in SYSTEMS:
                if name not in systems:
                    continue
                raw = systems[name](turn["question"], f"cmp-{s['id']}") if name == "new" else systems[name](turn["question"])   # 기존 챗봇은 턴을 이어 받지 못한다
                trow[name] = _entry(raw, ok=score_expect(turn["expect"], raw["answer"]))
            srow["turns"].append(trow)
        scen_rows.append(srow)
    return {"meta": {"date": date.today().isoformat(), "old_model": old_model, "questions": total, "scenarios": len(scen_rows), "systems": [n for n in SYSTEMS if n in systems]},
            "rows": rows, "scenarios": scen_rows}


# ---------- 요약
def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def summarize(result: dict) -> dict:
    out = {}
    for name in result["meta"]["systems"]:
        rows = [r[name] for r in result["rows"] if name in r]
        s = {"questions": len(rows), "errors": sum(1 for r in rows if r.get("error"))}
        for kind in ("calc", "ask", "fact"):
            scored = [r for r in rows if r.get("kind") == kind]
            s[kind] = (sum(1 for r in scored if r["correct"]), len(scored))
        s["evidence"] = (sum(1 for r in rows if r.get("evidence_shown")), len(rows))
        times = [r["ms"] for r in rows if not r.get("error")]
        s["p50_ms"], s["p90_ms"] = percentile(times, 0.5), percentile(times, 0.9)
        turns = [(t["turn"], t[name]["correct"]) for sc in result["scenarios"] for t in sc["turns"] if name in t]
        s["scenario_turns"] = (sum(1 for _, ok in turns if ok), len(turns))
        first = [ok for n, ok in turns if n == 1]
        later = [ok for n, ok in turns if n > 1]
        s["scenario_first"], s["scenario_later"] = (sum(first), len(first)), (sum(later), len(later))
        out[name] = s
    return out


def _fmt(pair, none="-") -> str:
    return none if pair is None else f"{pair[0]}/{pair[1]}"


def _secs(ms) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f}초"


def format_table(summary: dict, meta: dict | None = None) -> str:
    old, new = summary.get("old"), summary.get("new")

    def cell(system, key, fmt=_fmt):
        return "(실행 안 함)" if system is None else fmt(system[key])
    n_q = (new or old or {}).get("questions", 0)
    lines = ["| 비교 항목 | 기존 챗봇 | 이번 에이전트 |", "| --- | --- | --- |"]
    lines.append(f"| 계산이 맞는지 (정답 금액이 답에 있는 문항, {(new or old)['calc'][1]}개) | {cell(old, 'calc')} | {cell(new, 'calc')} |")
    lines.append(f"| 정보가 부족하면 되묻는지 ({(new or old)['ask'][1]}개) | {cell(old, 'ask')} | {cell(new, 'ask')} |")
    lines.append(f"| 약관의 핵심 사실이 맞는지 ({(new or old)['fact'][1]}개) | {cell(old, 'fact')} | {cell(new, 'fact')} |")
    old_ev = "(실행 안 함)" if old is None else f"0/{n_q} (근거 표시 기능 없음)"
    lines.append(f"| 근거를 보여 주는지 ({n_q}개) | {old_ev} | {cell(new, 'evidence')} |")
    lines.append(f"| 응답 시간 중앙값 · p90 ({n_q}개) | " + " | ".join("(실행 안 함)" if s is None else f"{_secs(s['p50_ms'])} · {_secs(s['p90_ms'])}" for s in (old, new)) + " |")
    lines.append(f"| 대화 중 값을 바꾼 사례 (6개 시나리오, 전체 턴) | {cell(old, 'scenario_turns')} | {cell(new, 'scenario_turns')} |")
    lines.append(f"| └ 첫 질문 | {cell(old, 'scenario_first')} | {cell(new, 'scenario_first')} |")
    lines.append(f"| └ 값을 바꾼 뒤 이어진 질문 | {cell(old, 'scenario_later')} | {cell(new, 'scenario_later')} |")
    lines.append(f"| 오류 · 시간 초과 | {cell(old, 'errors', lambda v: str(v))} | {cell(new, 'errors', lambda v: str(v))} |")
    return "\n".join(lines)


# ---------- 명령줄
def main(argv: list[str] | None = None, new_agent=None, old_ask=None) -> int:
    ap = argparse.ArgumentParser(description="기존 챗봇과 이번 에이전트를 같은 질문으로 비교")
    ap.add_argument("--old-url", default="http://127.0.0.1:8001", help="기존 챗봇 서버 주소 (기본 포트 8001)")
    ap.add_argument("--old-model", choices=("cloud", "local"), default="cloud", help="기존 챗봇의 답변 생성 모델")
    ap.add_argument("--skip-old", action="store_true")
    ap.add_argument("--skip-new", action="store_true")
    ap.add_argument("--limit", type=int, help="앞에서 N개 문항만 (빠른 점검)")
    ap.add_argument("--no-scenarios", action="store_true")
    ap.add_argument("--save")
    ap.add_argument("--rescore", help="저장된 결과를 서버 없이 다시 요약")
    ap.add_argument("--md", action="store_true", help="요약을 마크다운 표로만 출력")
    a = ap.parse_args(argv)
    if a.rescore:
        result = json.loads(Path(a.rescore).read_text(encoding="utf-8"))
        print(format_table(summarize(result), result["meta"]))
        return 0
    systems: dict = {}
    if not a.skip_old:
        ask = old_ask or (lambda q: ask_old(a.old_url, q, a.old_model))
        if old_ask is None and not old_alive(a.old_url):
            print(f"기존 챗봇 서버에 닿지 않습니다 ({a.old_url}). 그 레포에서 `python -m uvicorn app.main:app --port 8001`로 먼저 띄우세요 (--skip-old로 건너뛸 수 있습니다).")
            return 2
        systems["old"] = ask
    if not a.skip_new:
        if new_agent is None:
            from app.graph import Agent
            new_agent = Agent()
        scenarios_today = load_scenarios()["today"]
        systems["new"] = lambda q, session: ask_new(new_agent, q, session, scenarios_today)
    if not systems:
        print("실행할 시스템이 없습니다.")
        return 2
    questions = load_questions()[: a.limit] if a.limit else load_questions()
    scenarios = load_scenarios()
    if a.no_scenarios:
        scenarios = {**scenarios, "scenarios": []}
    result = run(questions, scenarios, systems, a.old_model)
    summary = summarize(result)
    print("\n" + format_table(summary, result["meta"]))
    if a.save:
        Path(a.save).parent.mkdir(parents=True, exist_ok=True)
        Path(a.save).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {a.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
