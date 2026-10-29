"""검증용 문항(eval/heldout/questions.json)으로 값 근거 대조를 잰다.

모델 없이 코드만 본다: '정답 모델이 질문에서 읽은 값을 그대로 넘겼다면 근거 대조가 통과시키는가'.
  said     질문에 적힌 값 (오늘 · 지금 포함). 빠지면 거짓 거절 = 사용자가 한 말을 또 묻게 됨
  derived  상대 날짜 해석이나 계산이 필요한 값 (다음 달 20일, 내년 4월 2일, 7월 말). 빠져도 설계상 의도한 동작이지만 되묻기가 늘어남
추가로 두 가지를 본다.
  probes   모델이 지어낼 법한 값 (정보가 없는 질문에 넣은 날짜 · 금리). 모두 빠져야 한다. 이전 측정에서 본 유형이라 낙관적인 확인이다.
  handoff  이 문항 중 상담원 연결 요청으로 오인되는 것 (0이어야 한다)

실행: python eval/heldout_guard.py [--save eval/heldout/guard_result.json]
검증용 문항의 규칙: 이 결과를 보고 규칙을 고치지 않는다 (questions.json의 rule 참고).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.answer_check import handoff_request  # noqa: E402
from app.grounding import ground_calc_args  # noqa: E402

QUESTIONS = ROOT / "eval" / "heldout" / "questions.json"

# 정보가 없는 질문에서 모델이 지어낼 법한 값 (이전 측정: 'N개월 전'을 날짜로 계산, 예시 금리 베끼기)
PROBES = [
    ("H3", "start_date", "2026-05-28", "'5개월쯤'을 날짜로 계산"), ("H3", "principal", 20_000_000, "'2천'을 2천만 원으로 해석"),
    ("H4", "start_date", "2025-12-01", "'작년 12월'에 일을 붙임"), ("H7", "start_date", "2026-02-28", "'8개월'을 날짜로 계산"),
    ("H9", "base_rate", 3.5, "예시 금리 베끼기"), ("H12", "base_rate", 3.5, "예시 금리 베끼기"),
    ("H19", "maturity_date", "2026-11-28", "'만기 한 달 전'을 날짜로 계산"), ("H20", "principal", 10_000_000, "원금을 지어냄"),
    ("H5", "maturity_date", "2026-12-28", "'두 달 남았는데'를 날짜로 계산"), ("H16", "start_date", "2026-04-28", "'6개월 만에'를 날짜로 계산"),
    ("H8", "termination_date", "2026-10-28", "오늘/지금이라고 안 했는데 오늘로 넣음"), ("H2", "maturity_date", "2027-03-10", "말하지 않은 만기일"),
]


def measure(path: Path = QUESTIONS) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    today = date.fromisoformat(data["today"])
    by_id = {q["id"]: q for q in data["questions"]}
    rows, kept = [], {"said": [0, 0], "derived": [0, 0]}
    for q in data["questions"]:
        args = {k: v["value"] for k, v in q["gold"].items()}
        dropped = ground_calc_args(args, [q["question"]], today)[1] if args else {}
        for field, v in q["gold"].items():
            kept[v["kind"]][1] += 1
            kept[v["kind"]][0] += field not in dropped
            if field in dropped:
                rows.append({"id": q["id"], "field": field, "value": v["value"], "kind": v["kind"], "question": q["question"]})
    probe_rows = []
    for qid, field, value, why in PROBES:
        if qid not in by_id:                                         # 문항 파일에 없는 id의 probe는 건너뛴다
            continue
        dropped = ground_calc_args({field: value}, [by_id[qid]["question"]], today)[1]
        probe_rows.append({"id": qid, "field": field, "value": value, "why": why, "dropped": field in dropped})
    handoff = [q["id"] for q in data["questions"] if handoff_request(q["question"])]
    return {"frozen_on": data["frozen_on"], "today": data["today"], "n_questions": len(data["questions"]),
            "said_kept": kept["said"], "derived_kept": kept["derived"], "rejected": rows,
            "probes": probe_rows, "probes_not_caught": [p for p in probe_rows if not p["dropped"]], "handoff_false_positives": handoff}


def print_report(res: dict, log=print) -> None:
    sk, dk = res["said_kept"], res["derived_kept"]
    log(f"검증용 {res['n_questions']}문항 (고정일 {res['frozen_on']}, 오늘 {res['today']})")
    log(f"  질문에 적힌 값(said):      근거 인정 {sk[0]}/{sk[1]}  -> 거짓 거절 {sk[1] - sk[0]}건")
    log(f"  해석이 필요한 값(derived): 근거 인정 {dk[0]}/{dk[1]}  -> 빠진 것 {dk[1] - dk[0]}건 (상대 날짜를 일부러 인정하지 않음)")
    for r in res["rejected"]:
        log(f"    - {r['id']} {r['field']}={r['value']} [{r['kind']}]  {r['question'][:46]}")
    caught = sum(1 for p in res["probes"] if p["dropped"])
    log(f"  지어낸 값 흉내(probes):     걸러냄 {caught}/{len(res['probes'])}")
    for p in res["probes_not_caught"]:
        log(f"    - 못 거름: {p['id']} {p['field']}={p['value']} ({p['why']})")
    log(f"  상담원 연결 요청으로 오인:   {len(res['handoff_false_positives'])}건 {res['handoff_false_positives'] or ''}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="검증용 문항으로 값 근거 대조를 잰다 (모델 없이)")
    ap.add_argument("--save")
    a = ap.parse_args(argv)
    res = measure()
    print_report(res)
    if a.save:
        Path(a.save).parent.mkdir(parents=True, exist_ok=True)
        Path(a.save).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {a.save}")


if __name__ == "__main__":
    main()
