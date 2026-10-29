"""검증용 문항(eval/heldout) 시험: 문항 파일의 형식, 근거 대조 회귀 확인, 실제 모델 실행기의 채점."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import heldout_guard  # noqa: E402
import heldout_run  # noqa: E402
from app.tool_calling import CALC_ARGS  # noqa: E402

DATA = json.loads((ROOT / "eval" / "heldout" / "questions.json").read_text(encoding="utf-8"))
QUESTIONS = DATA["questions"]


# ---------- 문항 파일 ----------
def test_question_file_shape():
    assert [q["id"] for q in QUESTIONS] == [f"H{i}" for i in range(1, 21)]
    assert len({q["question"] for q in QUESTIONS}) == 20 and all(q["question"].strip() for q in QUESTIONS)
    assert DATA["frozen_on"] == "2026-10-28" and DATA["today"] == "2026-10-28" and DATA["rule"] and DATA["prediction_before_measuring"]
    for q in QUESTIONS:
        assert q["expect"] in DATA["expect_legend"] and q["note"]


def test_gold_values_are_well_formed_and_labels_are_frozen():
    kinds = {"said": 0, "derived": 0}
    for q in QUESTIONS:
        for field, v in q["gold"].items():
            assert field in CALC_ARGS and v["kind"] in kinds
            kinds[v["kind"]] += 1
            if field == "principal":
                assert isinstance(v["value"], int) and v["value"] >= 1_000_000
            elif field == "base_rate":
                assert isinstance(v["value"], float) and 0 < v["value"] <= 20
            else:
                date.fromisoformat(v["value"])
    assert kinds == {"said": 24, "derived": 3}                       # 측정 전에 고정한 라벨 (바꾸면 검증용이 아니게 된다)


def test_no_question_asks_for_a_handoff():
    assert not any(heldout_guard.handoff_request(q["question"]) for q in QUESTIONS)


# ---------- 근거 대조 회귀: 검증용에서 달성한 수준보다 나빠지면 안 된다 ----------
def test_grounding_does_not_get_worse_on_the_heldout_questions():
    res = heldout_guard.measure()
    assert res["said_kept"] == [24, 24]                              # 질문에 적힌 값을 거짓으로 거절하지 않는다
    assert res["probes_not_caught"] == [] and res["handoff_false_positives"] == []
    assert res["n_questions"] == 20


def test_measure_counts_are_consistent():
    res = heldout_guard.measure()
    assert res["said_kept"][1] == 24 and res["derived_kept"][1] == 3
    assert len(res["rejected"]) == (24 - res["said_kept"][0]) + (3 - res["derived_kept"][0])
    assert len(res["probes"]) == 12


def test_guard_report_and_save(tmp_path, capsys):
    out = tmp_path / "r.json"
    heldout_guard.main(["--save", str(out)])
    text = capsys.readouterr().out
    assert "근거 인정 24/24" in text and "걸러냄 12/12" in text
    assert json.loads(out.read_text(encoding="utf-8"))["said_kept"] == [24, 24]


# ---------- 실제 모델 실행기의 채점 ----------
def q(qid):
    return next(x for x in QUESTIONS if x["id"] == qid)


def trace(name, status, **kw):
    return {"name": name, "args": kw.get("args", {}), "status": status, "ms": 1, "retried": False, "duplicate": False, "dropped": kw.get("dropped", {})}


def result(answer="", tr=(), error=None):
    return {"answer": answer, "error": error, "stop_reason": None, "trace": list(tr), "tool_calls": len(tr), "model_calls": 1, "ms": 5}


def test_asked_user_is_a_rough_check():
    assert heldout_run.asked_user("기본금리는 얼마였나요?") and heldout_run.asked_user("이 값을 알려주세요.") and heldout_run.asked_user("알려 주세요")
    assert not heldout_run.asked_user("세전 이자는 273,304원입니다.")


@pytest.mark.parametrize("qid,res,ok,flags", [
    ("H9", result("적용됩니다.", [trace("search_terms", "ok")]), True, []),
    ("H9", result("적용됩니다."), False, ["wrong_behavior"]),                                        # 검색 없이 답함
    ("H6", result("기본금리를 알려주세요?", [trace("calc_interest", "need_base_rate")]), True, []),
    ("H6", result("이자는 1원입니다.", [trace("calc_interest", "ok")]), True, ["fabricated_ok"]),       # 계산이 ok로 끝나면 지어낸 값이 통과한 것
    ("H8", result("신규일을 알려주세요."), True, []),                                                    # calc_partial: 도구 없이 되물어도 통과
    ("H8", result("이자는 계산할 수 없습니다."), False, ["wrong_behavior"]),
    ("H3", result("원금 단위를 알려주세요.", [trace("calc_interest", "missing_info")]), True, []),
    ("H3", result("2천만 원이면 이자는…", [trace("calc_interest", "ok")]), False, ["wrong_behavior", "fabricated_ok"]),   # 되묻지 않고 지어낸 값으로 계산
    ("H12", result("고객센터에 문의해 주세요?", [trace("search_terms", "ok")]), True, []),
    ("H12", result("확인할 수 있습니다.", [trace("search_terms", "ok")]), False, ["wrong_behavior"]),    # search_ask는 되묻기도 필요
    ("H9", result("적용됩니다. 利息", [trace("search_terms", "ok")]), True, ["foreign_script"]),
    ("H9", result("문의는 1544-1111로.", [trace("search_terms", "ok")]), True, ["phone_in_answer"]),
])
def test_grade(qid, res, ok, flags):
    g = heldout_run.grade(q(qid), res)
    assert g["behavior_ok"] is ok and g["flags"] == flags


def test_grade_model_error_is_not_a_behavior_pass():
    g = heldout_run.grade(q("H9"), result(None, [trace("search_terms", "ok")], error="모델 호출 실패"))
    assert g["behavior_ok"] is False and g["flags"] == ["model_error"]


def test_grade_notices_dropped_values():
    g = heldout_run.grade(q("H8"), result("알려주세요", [trace("calc_interest", "missing_info", dropped={"base_rate": 3.5})]))
    assert g["dropped_any"] is True


class FakeAgent:
    def __init__(self):
        self.calls = []

    def ask(self, question, session_id, today):
        self.calls.append((session_id, today))
        qid = session_id.split("-")[1]
        if q(qid)["expect"].startswith("search"):
            return result("규칙 안내?", [trace("search_terms", "ok", args={"query": "중도해지"})])
        return result("값을 알려주세요.", [trace("calc_interest", "missing_info", args={"principal": 1}, dropped={"base_rate": 3.5})])


def test_run_asks_every_question_in_a_fresh_session_with_the_frozen_date():
    agent = FakeAgent()
    out = heldout_run.run(agent=agent, log=None)
    assert len(agent.calls) == 20 and len({c[0] for c in agent.calls}) == 20 and {c[1] for c in agent.calls} == {"2026-10-28"}
    s = out["summary"]
    assert s["n"] == 20 and s["behavior_ok"] == s["n"] and set(s["by_expect"]) == {"ask", "calc", "calc_partial", "search", "search_ask"}
    assert sum(v[1] for v in s["by_expect"].values()) == 20


def test_run_only_and_log_format():
    lines = []
    out = heldout_run.run(agent=FakeAgent(), only={"H9", "H1"}, log=lines.append)
    assert [r["id"] for r in out["rows"]] == ["H1", "H9"]
    assert any("말하지 않은 값 제외 base_rate" in ln for ln in lines) and any("search_terms(query=중도해지)->ok" in ln for ln in lines)


def test_main_saves_the_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(heldout_run, "Agent", lambda: FakeAgent())
    out = tmp_path / "run.json"
    heldout_run.main(["--save", str(out), "--only", "H9,H12"])
    text = capsys.readouterr().out
    assert "검증용 2문항" in text and "저장:" in text
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["kind"] == "heldout_run" and [r["id"] for r in saved["rows"]] == ["H9", "H12"] and saved["frozen_on"] == "2026-10-28"


# ---------- 측정 코드 자체의 시험 (합성 문항: 측정기가 제대로 재는지) ----------
def synthetic(tmp_path, question, gold):
    data = {"frozen_on": "2026-10-28", "today": "2026-10-28",
            "questions": [{"id": "X1", "question": question, "expect": "ask", "gold": gold, "note": "n"}]}
    p = tmp_path / "q.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_measure_really_calls_the_guard(tmp_path):
    p = synthetic(tmp_path, "안녕하세요", {"principal": {"value": 10_000_000, "kind": "said"}, "start_date": {"value": "2026-01-01", "kind": "derived"}})
    res = heldout_guard.measure(p)
    assert res["said_kept"] == [0, 1] and res["derived_kept"] == [0, 1]
    assert {r["field"] for r in res["rejected"]} == {"principal", "start_date"}
    ok = synthetic(tmp_path, "1000만원을 2026-01-01에 넣었어요", {"principal": {"value": 10_000_000, "kind": "said"}, "start_date": {"value": "2026-01-01", "kind": "said"}})
    assert heldout_guard.measure(ok)["said_kept"] == [2, 2]


def test_measure_reports_probes_that_slip_through(tmp_path, monkeypatch):
    p = synthetic(tmp_path, "1000만원을 넣었어요", {})
    monkeypatch.setattr(heldout_guard, "PROBES", [("X1", "principal", 10_000_000, "질문에 있는 값"), ("X1", "base_rate", 3.5, "질문에 없는 값")])
    res = heldout_guard.measure(p)
    assert [x["dropped"] for x in res["probes"]] == [False, True]
    assert [x["field"] for x in res["probes_not_caught"]] == ["principal"]


def test_measure_reports_handoff_false_positives(tmp_path):
    res = heldout_guard.measure(synthetic(tmp_path, "상담원 연결해줘", {}))
    assert res["handoff_false_positives"] == ["X1"]
