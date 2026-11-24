"""CI용 평가 게이트 (모델 · 색인 없이): 평가 수치가 README와 어긋나면 어긋난 항목을 말하고 종료 코드 1.

실행: python eval/ci_gate.py
기대값은 README의 개발용 수치(eval_core_3, 2026-11-04)다. 바꿀 때는 이유를 커밋 메시지에 남긴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import dev_guard  # noqa: E402
import evaluate  # noqa: E402
import heldout_guard  # noqa: E402
import heldout_tools  # noqa: E402
import run_eval  # noqa: E402

RUNS = "eval/runs/eval_core_3.json"
EXPECTED = {"behavior_ok": (41, 46), "clean": (40, 46), "gold_chunk_in_evidence": (14, 14)}  # (hit, n), README 평가 절
EXPECTED_PROBES = 12  # 지어낸 값 흉내 개수: 0개를 통과로 보지 않으려는 확인


def _dev() -> list[str]:
    r = dev_guard.measure()
    return [f"dev_guard.{k}: {r[k]} (0이어야 함)" for k in ("false_rejections", "missed") if r[k]]


def _heldout_guard() -> list[str]:
    r = heldout_guard.measure()
    out = []
    if len(r["probes"]) != EXPECTED_PROBES:
        out.append(f"heldout_guard.probes: {len(r['probes'])}개 ({EXPECTED_PROBES}개여야 함)")
    if r["probes_not_caught"]:
        out.append(f"heldout_guard.probes_not_caught: {[p['id'] for p in r['probes_not_caught']]}")
    if r["handoff_false_positives"]:
        out.append(f"heldout_guard.handoff_false_positives: {r['handoff_false_positives']}")
    return out


def _freeze() -> list[str]:
    return [f"heldout_tools.verify: {p}" for p in heldout_tools.verify()]


def _rescore(path: str) -> list[str]:
    rows, info = run_eval.rescore(path, run_eval.load())
    out = [f"rescore.skipped: {info['skipped']} (문항 글이 바뀌어 건너뜀)"] if info["skipped"] else []
    overall = evaluate.summarize(rows)["overall"]
    for key, want in EXPECTED.items():
        got = (overall[key]["hit"], overall[key]["n"])
        if got != want:
            out.append(f"rescore.{key}: {got[0]}/{got[1]} (기대 {want[0]}/{want[1]})")
    return out


def check_all(runs_path: str = RUNS) -> list[str]:
    out = []
    for name, gate in (("dev_guard", _dev), ("heldout_guard", _heldout_guard), ("heldout_tools", _freeze),
                       ("rescore", lambda: _rescore(runs_path))):
        try:
            out += gate()
        except Exception as e:  # 한 게이트의 예외가 나머지 결과를 가리지 않게 한다
            out.append(f"{name}: 예외 {type(e).__name__}: {e}")
    return out


def main(argv: list[str] | None = None) -> int:
    problems = check_all()
    for p in problems:
        print("FAIL", p)
    print("평가 게이트: " + ("실패 %d건" % len(problems) if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
