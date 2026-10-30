"""검증용(held_out) 문항 관리 시험 (10일차): 받기 · 정답 확인 · 고정 · 고정이 깨졌는지 확인. 고정한 문항이 어떤 경로로도 바뀌거나 지워지면 안 된다."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import build_questions as bq  # noqa: E402
from eval import heldout_tools as ht  # noqa: E402

DEV_ROW = {"id": "Q001", "split": "dev", "core": True, "source": ["x:1"], "category": "rule", "question": "개발용 질문", "today": "2026-10-28",
           "gold": {"behavior": "search", "tools": ["search_terms"], "tools_allowed": []}, "tags": [], "note": ""}
LABEL = {"category": "rule", "gold": {"behavior": "search", "tools": ["search_terms"], "tools_allowed": [], "chunks": ["hana-deposit-sheet__early_termination"]}}


@pytest.fixture
def files(tmp_path, monkeypatch):
    q, m = tmp_path / "questions.jsonl", tmp_path / "manifest.json"
    ht.save_rows([DEV_ROW], q)
    monkeypatch.setattr(ht, "QUESTIONS", q)
    monkeypatch.setattr(ht, "MANIFEST", m)
    return q, m


def label_all(q: Path, **override):
    rows = ht.load_rows(q)
    for r in rows:
        if r["split"] == "held_out":
            r.update(LABEL | override)
    ht.save_rows(rows, q)


# ---------- 받기 ----------
def test_clean_lines_strips_numbering_and_bullets_and_skips_blanks_and_repeats():
    raw = "1. 첫째 질문\n2) 둘째 질문\n- 셋째 질문\n\n  • 넷째 질문\n첫째 질문\n5: 여섯째 질문  \n"
    assert ht.clean_lines(raw) == ["첫째 질문", "둘째 질문", "셋째 질문", "넷째 질문", "여섯째 질문"]
    assert ht.clean_lines("") == [] and ht.clean_lines("\n\n") == []


def test_intake_adds_unlabeled_held_out_rows_with_the_writer_and_continues_the_numbering(files):
    q, _ = files
    added = ht.intake("1. 새 질문 하나\n2. 새 질문 둘", "친구 A (시스템을 모름)")
    assert [r["id"] for r in added] == ["X001", "X002"]
    r = added[0]
    assert r["split"] == "held_out" and r["gold"] is None and r["category"] is None and r["core"] is False and r["writer"] == "친구 A (시스템을 모름)" and r["source"] == ["outsider"]
    assert [x["id"] for x in ht.intake("새 질문 셋", "친구 B")] == ["X003"]
    assert [x["id"] for x in ht.load_rows(q)] == ["Q001", "X001", "X002", "X003"]


def test_intake_skips_questions_that_already_exist_even_the_dev_ones(files):
    added = ht.intake("개발용 질문\n새 질문\n새 질문", "친구")
    assert [r["question"] for r in added] == ["새 질문"]
    assert ht.intake("개발용 질문", "친구") == []


# ---------- 정답 확인 · 고정 ----------
def test_labeling_problems_name_the_unfinished_rows(files):
    ht.intake("질문 하나\n질문 둘\n질문 셋", "친구")
    rows = ht.load_rows()
    rows[1].update(LABEL)
    rows[2].update(category="bogus", gold=LABEL["gold"])
    rows[3].update(category="rule", gold={"behavior": "nope", "tools": []})
    problems = ht.problems_labeling(rows)
    assert len(problems) == 2 and "X002" in problems[0] and "X003" in problems[1] and not any("X001" in p for p in problems)
    assert ht.problems_labeling([DEV_ROW]) == []                           # 개발용은 점검하지 않는다


def test_freezing_needs_held_out_questions_and_all_their_gold(files):
    with pytest.raises(RuntimeError, match="검증용 문항이 없습니다"):
        ht.freeze("Claude")
    ht.intake("질문 하나", "친구")
    with pytest.raises(RuntimeError, match="정답이 덜 쓰였습니다"):
        ht.freeze("Claude")
    assert not ht.MANIFEST.exists()                                        # 실패한 고정은 아무것도 남기지 않는다


def test_freezing_records_the_fingerprints_the_labeler_and_can_only_happen_once(files):
    q, m = files
    ht.intake("질문 하나\n질문 둘", "친구")
    label_all(q)
    data = ht.freeze("Claude (질문만 보고)", today="2026-10-30")
    assert data["count"] == 2 and data["frozen_on"] == "2026-10-30" and data["labeler"] == "Claude (질문만 보고)" and data["runs"] == []
    assert set(data["fingerprints"]) == {"X001", "X002"} and all(len(v) == 64 for v in data["fingerprints"].values())
    assert json.loads(m.read_text(encoding="utf-8")) == data
    with pytest.raises(RuntimeError, match="이미 고정"):
        ht.freeze("다시")
    with pytest.raises(RuntimeError, match="이미 고정"):
        ht.intake("고정 뒤에 받은 질문", "친구")                               # 고정 뒤에는 문항을 더 받지 못한다


# ---------- 고정이 깨졌는지 ----------
def frozen(files):
    q, _ = files
    ht.intake("질문 하나\n질문 둘", "친구")
    label_all(q)
    ht.freeze("Claude")
    return q


def test_verify_is_clean_right_after_freezing_and_reports_a_missing_manifest(files):
    q, _ = files
    assert "고정되지 않았습니다" in ht.verify()[0]
    frozen(files)
    assert ht.verify() == []


@pytest.mark.parametrize("change", [
    lambda r: r.update(question=r["question"] + "?"), lambda r: r["gold"].update(behavior="scope"), lambda r: r["gold"]["tools"].append("calc_interest"),
    lambda r: r.update(category="calc"), lambda r: r.update(today="2026-10-29"),
])
def test_changing_a_frozen_question_or_its_gold_is_detected(files, change):
    q = frozen(files)
    rows = ht.load_rows(q)
    change(rows[1])
    ht.save_rows(rows, q)
    assert any("X001" in p and "바뀌었습니다" in p for p in ht.verify())


def test_removing_or_adding_a_held_out_row_after_freezing_is_detected(files):
    q = frozen(files)
    rows = ht.load_rows(q)
    ht.save_rows([r for r in rows if r["id"] != "X002"], q)
    assert any("X002" in p and "사라졌습니다" in p for p in ht.verify())
    ht.save_rows(rows + [{**rows[1], "id": "X009", "question": "몰래 넣은 질문"}], q)
    assert any("X009" in p and "추가" in p for p in ht.verify())


def test_notes_and_tags_may_change_after_freezing_but_not_what_is_measured(files):
    q = frozen(files)
    rows = ht.load_rows(q)
    rows[1].update(note="메모를 달았다", tags=["메모"], core=True)
    ht.save_rows(rows, q)
    assert ht.verify() == []


# ---------- 실행 기록 · 상태 ----------
def test_runs_are_recorded_in_the_manifest_and_shown_in_the_status(files):
    frozen(files)
    assert ht.runs() == []
    ht.record_run("eval/runs/a.json", {"overall": {"behavior_ok": {"hit": 1, "n": 2}, "clean": {"hit": 1, "n": 2}}}, today="2026-10-31")
    assert ht.runs() == [{"date": "2026-10-31", "file": "eval/runs/a.json", "behavior_ok": {"hit": 1, "n": 2}, "clean": {"hit": 1, "n": 2}}]
    s = ht.status()
    assert "검증용 문항 2개 (정답 있음 2개)" in s and "실행 1회" in s and "이상 없음" in s and "(정답 작성: Claude)" in s


def test_status_before_freezing_and_with_a_broken_freeze(files):
    q, _ = files
    ht.intake("질문 하나", "친구")
    assert "아직 고정되지 않았습니다" in ht.status() and "정답 있음 0개" in ht.status()
    label_all(q)
    ht.freeze("Claude")
    rows = ht.load_rows(q)
    rows[1]["question"] = "바꿈"
    ht.save_rows(rows, q)
    assert "문제 1건" in ht.status()


# ---------- 명령줄 ----------
def test_the_command_line_flow(files, tmp_path, capsys):
    raw = tmp_path / "raw.txt"
    raw.write_text("1. 첫 질문\n2. 둘째 질문\n", encoding="utf-8")
    assert ht.main(["intake", str(raw), "--writer", "친구 A"]) == 0 and "2문항을 받았어요 (X001~X002)" in capsys.readouterr().out
    assert ht.main(["intake", str(raw), "--writer", "친구 A"]) == 0 and "새로 받은 문항이 없어요" in capsys.readouterr().out
    assert ht.main(["freeze", "--labeler", "Claude"]) == 1 and "정답이 덜 쓰였습니다" in capsys.readouterr().out
    label_all(files[0])
    assert ht.main(["freeze", "--labeler", "Claude"]) == 0 and "2문항을 고정했어요" in capsys.readouterr().out
    assert ht.main(["verify"]) == 0 and "이상 없음" in capsys.readouterr().out
    assert ht.main(["freeze", "--labeler", "다시"]) == 1
    rows = ht.load_rows()
    rows[1]["question"] = "바꿈"
    ht.save_rows(rows)
    assert ht.main(["verify"]) == 1 and "X001" in capsys.readouterr().out
    assert ht.main(["status"]) == 0 and "검증용 문항 2개" in capsys.readouterr().out


# ---------- 빌더가 검증용을 지우지 않는다 ----------
def test_rebuilding_the_dev_set_never_touches_the_held_out_rows(tmp_path, monkeypatch):
    out = tmp_path / "questions.jsonl"
    monkeypatch.setattr(bq, "OUT", out)
    monkeypatch.setattr(ht, "QUESTIONS", out)
    monkeypatch.setattr(ht, "MANIFEST", tmp_path / "m.json")
    assert bq.main([]) == 0
    dev_only = out.read_text(encoding="utf-8")
    ht.intake("새 질문 하나\n새 질문 둘", "친구")
    label_all(out)
    held_before = [r for r in ht.load_rows(out) if r["split"] == "held_out"]
    ht.freeze("Claude")
    assert bq.main([]) == 0                                                # 다시 만들어도
    assert [r for r in ht.load_rows(out) if r["split"] == "held_out"] == held_before and ht.verify() == []
    assert out.read_text(encoding="utf-8").startswith(dev_only)            # 개발용 부분은 그대로, 검증용은 뒤에 붙어 있다
    assert bq.main(["--check"]) == 0                                       # 검증용이 있어도 개발용 비교는 통과한다
