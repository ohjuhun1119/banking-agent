"""개발용 문항(eval/dev) 회귀 시험: 고쳐서 얻은 수준보다 나빠지면 안 된다. (개발용 점수는 일반화 증거가 아니다.)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import dev_guard  # noqa: E402

DATA = json.loads((Path(__file__).resolve().parents[1] / "eval" / "dev" / "grounding_phrasings.json").read_text(encoding="utf-8"))


def test_dev_set_shape():
    assert len(DATA["keep"]) == 30 and len(DATA["drop"]) == 8 and DATA["today"] == "2026-10-28"
    assert all(c["text"] and c["args"] for c in DATA["keep"] + DATA["drop"])


def test_dev_set_has_no_false_rejections_and_misses_nothing():
    r = dev_guard.measure()
    assert r["false_rejections"] == [] and r["missed"] == []
    assert (r["keep_total"], r["drop_total"]) == (30, 8)


def test_measure_really_checks_both_directions(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"today": "2026-10-28", "keep": [{"text": "안녕", "args": {"principal": 10_000_000}}],
                             "drop": [{"text": "1000만원 넣었어요", "args": {"principal": 10_000_000}}]}, ensure_ascii=False), encoding="utf-8")
    r = dev_guard.measure(p)
    assert len(r["false_rejections"]) == 1 and r["false_rejections"][0]["dropped"] == {"principal": 10_000_000}
    assert len(r["missed"]) == 1


def test_main_prints_the_summary(capsys):
    dev_guard.main()
    assert "거짓 거절 0건" in capsys.readouterr().out
