"""CI 평가 게이트 시험: 실제 레포는 통과하고, 어긋나면 어긋난 항목을 말하며 실패한다."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import ci_gate  # noqa: E402
import dev_guard  # noqa: E402
import heldout_tools  # noqa: E402


def test_the_real_repo_passes_every_gate():
    assert ci_gate.check_all() == []


def test_a_score_below_the_expected_value_is_reported(monkeypatch):
    monkeypatch.setattr(ci_gate, "EXPECTED", {"behavior_ok": (42, 46)})
    out = " ".join(ci_gate.check_all())
    assert "behavior_ok" in out and "41" in out and "42" in out


def test_skipped_rows_in_the_saved_run_fail_the_gate(tmp_path):
    data = json.loads((ROOT / "eval" / "runs" / "eval_core_3.json").read_text(encoding="utf-8"))
    data["rows"][0]["question"] += " (문항 글이 바뀜)"
    copy = tmp_path / "run.json"
    copy.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert any("skipped" in line for line in ci_gate.check_all(str(copy)))


def test_a_missing_freeze_manifest_fails(monkeypatch):
    monkeypatch.setattr(heldout_tools, "verify", lambda: ["고정되지 않았습니다 (freeze 필요)"])
    assert any("고정되지 않았습니다" in line for line in ci_gate.check_all())


def test_one_gate_raising_does_not_hide_the_others(monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(dev_guard, "measure", boom)
    monkeypatch.setattr(heldout_tools, "verify", lambda: ["고정이 깨졌습니다"])
    out = ci_gate.check_all()
    assert any("dev_guard" in line for line in out)
    assert any("고정이 깨졌습니다" in line for line in out)


def test_main_returns_1_on_failure_and_0_on_success(monkeypatch):
    assert ci_gate.main([]) == 0
    monkeypatch.setattr(heldout_tools, "verify", lambda: ["x"])
    assert ci_gate.main([]) == 1


def test_an_empty_probe_list_fails_the_gate(monkeypatch):
    import heldout_guard
    real = heldout_guard.measure()
    monkeypatch.setattr(heldout_guard, "measure", lambda: {**real, "probes": [], "probes_not_caught": []})
    assert any("probes" in line for line in ci_gate.check_all())
