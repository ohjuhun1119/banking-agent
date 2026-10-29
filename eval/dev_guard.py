"""개발용 문항(eval/dev/grounding_phrasings.json)으로 값 근거 대조를 잰다 (모델 없이).

keep  사용자가 말한 값을 정확히 넘겼을 때 근거로 인정하는가 (빠지면 거짓 거절)
drop  모델이 틀린 값을 넘겼을 때 걸러내는가 (통과하면 못 거름)
개발용이라 결과를 보고 고친다. 고친 수치는 일반화 증거가 아니다: 검증용은 eval/heldout_guard.py.
실행: python eval/dev_guard.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.grounding import ground_calc_args  # noqa: E402

PATH = ROOT / "eval" / "dev" / "grounding_phrasings.json"


def measure(path: Path = PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    today = date.fromisoformat(data["today"])
    false_rejections, missed = [], []
    for c in data["keep"]:
        dropped = ground_calc_args(c["args"], [c["text"]], today)[1]
        if dropped:
            false_rejections.append({"text": c["text"], "args": c["args"], "dropped": dropped})
    for c in data["drop"]:
        if not ground_calc_args(c["args"], [c["text"]], today)[1]:
            missed.append({"text": c["text"], "args": c["args"]})
    return {"keep_total": len(data["keep"]), "drop_total": len(data["drop"]), "false_rejections": false_rejections, "missed": missed}


def main() -> None:
    r = measure()
    print(f"개발용: 말한 값 {r['keep_total']}건 중 거짓 거절 {len(r['false_rejections'])}건, 틀린 값 {r['drop_total']}건 중 못 거름 {len(r['missed'])}건")
    for x in r["false_rejections"]:
        print("  거짓 거절:", x["text"], x["args"], "->", x["dropped"])
    for x in r["missed"]:
        print("  못 거름:", x["text"], x["args"])


if __name__ == "__main__":
    main()
