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


H6_ARGS = {"principal": 7_000_000, "start_date": "2025-09-15", "maturity_date": "2026-09-15", "termination_date": "2026-07-31"}


def result(answer="", tr=(), error=None):
    return {"answer": answer, "error": error, "stop_reason": None, "trace": list(tr), "tool_calls": len(tr), "model_calls": 1, "ms": 5}


def test_asked_user_is_a_rough_check():
    assert heldout_run.asked_user("기본금리는 얼마였나요?") and heldout_run.asked_user("이 값을 알려주세요.") and heldout_run.asked_user("알려 주세요")
    assert not heldout_run.asked_user("세전 이자는 273,304원입니다.")


@pytest.mark.parametrize("qid,res,ok,flags", [
    ("H9", result("적용됩니다.", [trace("search_terms", "ok")]), True, []),
    ("H9", result("적용됩니다."), False, ["wrong_behavior"]),                                        # 검색 없이 답함
    ("H6", result("기본금리를 알려주세요?", [trace("calc_interest", "need_base_rate", args=H6_ARGS)]), True, []),
    ("H6", result("이자는 1원입니다.", [trace("calc_interest", "ok", args=H6_ARGS)]), True, ["fabricated_ok"]),       # 계산이 ok로 끝나면 지어낸 값이 통과한 것
    ("H8", result("신규일을 알려주세요."), True, []),                                                    # calc_partial: 도구 없이 되물어도 통과
    ("H8", result("이자는 계산할 수 없습니다."), False, ["wrong_behavior"]),
    ("H3", result("원금 단위를 알려주세요.", [trace("calc_interest", "missing_info", args={"termination_date": "2026-10-28"})]), True, []),
    ("H3", result("2천만 원이면 이자는…", [trace("calc_interest", "ok", args={"termination_date": "2026-10-28"})]), False, ["wrong_behavior", "fabricated_ok"]),   # 되묻지 않고 지어낸 값으로 계산
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


# ---------- 강화한 채점기로 수정 전 실제 기록을 다시 채점 (기준선) ----------
FIXTURE = ROOT / "tests" / "fixtures" / "heldout_before_fix.json"
BASELINE_FLAGS = {          # 수정 전 실제 모델 결과를 지금의 채점 기준으로 본 것: 수정 후 이보다 나빠지면 안 된다
    "H1": ["omitted_stated:maturity_date", "reasked_stated:maturity_date"], "H2": ["omitted_derived:termination_date"], "H3": ["ungrounded_in_answer"],
    "H4": ["ungrounded_in_answer"], "H5": ["ungrounded_in_answer"], "H6": ["omitted_stated:maturity_date", "omitted_derived:termination_date"],
    "H7": ["wrong_behavior"],            # 출력에 잘린 답변이라 되묻는 부분이 안 보임 (저장된 전문으로 재채점하면 다를 수 있다)
    "H8": ["asks_format"], "H9": [], "H10": ["wrong_behavior", "unsupported_not_found"], "H11": ["wrong_behavior", "unsupported_not_found"], "H12": ["wrong_behavior"],
    "H13": ["omitted_derived:maturity_date"], "H14": ["wrong_behavior", "ungrounded_in_answer", "tool_syntax"], "H15": [], "H16": ["ungrounded_in_answer", "note_leak"],
    "H17": ["wrong_behavior"], "H18": [], "H19": [], "H20": ["ungrounded_in_answer"],
}


def test_the_strengthened_grader_sees_what_the_lenient_one_missed():
    out = heldout_run.rescore(FIXTURE, log=None)
    assert {r["id"]: r["flags"] for r in out["rows"]} == BASELINE_FLAGS
    s = out["summary"]
    assert s["n"] == 20 and s["all_clean"] == 4 and s["flags"]["ungrounded_in_answer"] == 6 and s["flags"]["omitted_derived"] == 3
    assert out["kind"] == "heldout_rescore" and out["rows"][0]["trace"] and out["rows"][0]["question"] == HELD_Q["H1"]


HELD_Q = {q["id"]: q["question"] for q in QUESTIONS}


def test_effective_fields_subtract_what_the_code_dropped_and_add_what_it_filled():
    calls = [{"args": {"principal": 1, "start_date": "2026-05-28", "base_rate": None}, "dropped": {"start_date": "2026-05-28"}, "filled": {"maturity_date": "2027-01-01"}}]
    assert heldout_run.effective_fields(calls) == {"principal", "maturity_date"}
    assert heldout_run.effective_fields([{"args": {"principal": 1}}, {"args": {"start_date": "2026-01-01"}}]) == {"principal", "start_date"}


def test_a_filled_maturity_date_clears_the_omission_flag():
    """수정 후 기대: 모델이 만기일을 빠뜨려도 코드가 채우면 '빠뜨림' 표시가 사라진다."""
    row = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"][0]                       # H1
    assert "omitted_stated:maturity_date" in heldout_run.grade(q("H1"), {**row, "today": "2026-10-28"})["flags"]
    row["trace"][0]["filled"] = {"maturity_date": "2026-06-01"}
    row["answer"] = "해지일이 만기일 이후라 중도해지가 아니에요."
    assert heldout_run.grade(q("H1"), {**row, "today": "2026-10-28"})["flags"] == []


@pytest.mark.parametrize("qid,answer,flag", [
    ("H9", "약관에 따라 가능해요. 만기일을 알려주세요.", None),                                  # 이 문항에는 만기일 gold가 없다
    ("H1", "해지일이 만기일 이후라 중도해지가 아니에요.", None),                                  # 되묻는 문장이 아니면 표시하지 않는다
    ("H1", "만기일은 언제인가요?", "reasked_stated:maturity_date"), ("H1", "원금이 얼마였는지 알려주세요.", "reasked_stated:principal"),
])
def test_reasked_stated_only_when_asking_for_a_value_the_user_already_gave(qid, answer, flag):
    tr = [trace("calc_interest", "missing_info", args={"principal": 10_000_000, "start_date": "2025-06-01", "maturity_date": "2026-06-01", "termination_date": "2026-10-28"})]
    flags = heldout_run.grade(q(qid), {**result(answer, tr), "today": "2026-10-28"})["flags"]
    assert (flag in flags) if flag else not any(f.startswith("reasked") for f in flags)


def test_not_found_claim_is_fine_after_a_not_found_search_and_ungrounded_values_are_skipped_for_search_turns():
    flags = heldout_run.grade(q("H9"), {**result("약관에서 확인되지 않습니다.", [trace("search_terms", "not_found")]), "today": "2026-10-28"})["flags"]
    assert "unsupported_not_found" not in flags
    flags = heldout_run.grade(q("H9"), {**result("최소 가입금액은 100만원이에요. 2026-03-20 기준이에요.", [trace("search_terms", "ok")]), "today": "2026-10-28"})["flags"]
    assert "ungrounded_in_answer" not in flags


def test_rescore_main_prints_and_saves(tmp_path, capsys):
    out = tmp_path / "rescored.json"
    heldout_run.main(["--rescore", str(FIXTURE), "--save", str(out)])
    text = capsys.readouterr().out
    assert "깨끗한 것 4/20" in text and "ungrounded_in_answer 6" in text and "저장:" in text
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["all_clean"] == 4


# ---------- 수정 전 실제 모델이 한 계산 호출을 수정된 그래프에 그대로 다시 넣어 보기 (모델이 아니라 기록의 재생) ----------
from langchain_core.messages import AIMessage  # noqa: E402

from app.graph import Agent  # noqa: E402

REPLAY_EXPECTED = {            # 문항: (수정 전 상태, 수정 후 상태, 코드가 채운 칸, 코드가 뺀 칸)
    "H1": ("missing_info", "not_early_termination", {"maturity_date"}, set()),          # 만기일을 또 묻던 것이 해지일이 만기 이후라는 안내로
    "H2": ("missing_info", "missing_info", {"termination_date"}, {"termination_date"}),  # 모델이 틀리게 계산한 2026-04-20을 빼고 2026-11-20을 채움
    "H3": ("missing_info", "missing_info", set(), {"principal", "start_date"}),
    "H4": ("missing_info", "missing_info", set(), {"principal", "start_date"}),
    "H5": ("missing_info", "missing_info", set(), {"start_date", "maturity_date"}),
    "H6": ("missing_info", "missing_info", {"maturity_date"}, {"termination_date"}),     # 만기일은 채우고, '7월 말'은 정확한 날짜를 되묻는다
    "H8": ("invalid_input", "missing_info", set(), {"termination_date"}),                # 'YYYY-MM-DD로 입력해 주세요' 대신 값 부족 안내
    "H13": ("missing_info", "need_base_rate", {"maturity_date"}, set()),                 # 핵심 경로(기본금리 되묻기)에 도달
    "H16": ("missing_info", "missing_info", set(), {"start_date", "maturity_date", "termination_date"}),
    "H20": ("missing_info", "missing_info", set(), {"principal", "start_date"}),
}


class ReplayModel:
    def __init__(self, call):
        self.call, self.n = call, 0

    def invoke(self, messages):
        self.n += 1
        if self.n == 1:
            return AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": self.call["args"], "id": "c1"}])
        return AIMessage(content="값을 알려주세요.")


@pytest.mark.parametrize("qid", list(REPLAY_EXPECTED))
def test_recorded_real_model_calls_now_end_in_better_tool_states(qid):
    before, after, filled, dropped = REPLAY_EXPECTED[qid]
    row = next(r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"] if r["id"] == qid)
    call = next(t for t in row["trace"] if t["name"] == "calc_interest")
    assert call["status"] == before                                                       # 기록이 맞는지
    agent = Agent(model=ReplayModel(call), search_fn=lambda query, **kw: {"status": "not_found", "query": query, "results": []})
    res = agent.ask(HELD_Q[qid], "replay", "2026-10-28")
    t = res["trace"][0]
    assert t["status"] == after and set(t["filled"]) == filled and set(t["dropped"]) == dropped
    assert t["status"] != "ok"                                                            # 이 20문항은 어느 것도 ok로 끝나면 안 된다


# ---------- 채점기 보정 (수정 후 실제 모델 결과를 본 뒤): 오탐 제거와 새 점검 ----------
AFTER_FIXTURE = ROOT / "tests" / "fixtures" / "heldout_after_fix.json"
AFTER_FLAGS = {             # 수정 후 실제 모델 결과(화면 출력에서 옮긴 12문항)를 보정한 채점기로 본 것
    "H1": [], "H3": [], "H4": [], "H5": [], "H8": [], "H9": [], "H13": [], "H15": [], "H16": [], "H18": [],
    "H19": ["wrong_behavior"], "H20": ["unknown_arg:filled_args", "promises_after_tax"],
}


def test_the_corrected_grader_on_the_after_fix_run():
    out = heldout_run.rescore(AFTER_FIXTURE, log=None)
    assert {r["id"]: r["flags"] for r in out["rows"]} == AFTER_FLAGS
    assert out["summary"]["all_clean"] == 10 and out["summary"]["n"] == 12


def test_the_before_fix_baseline_does_not_change_with_the_corrected_grader():
    out = heldout_run.rescore(FIXTURE, log=None)
    assert {r["id"]: r["flags"] for r in out["rows"]} == BASELINE_FLAGS and out["summary"]["all_clean"] == 4


@pytest.mark.parametrize("sentence,label,expected", [
    ("만기일을 알려주시면 정확한 계산이 가능합니다", "만기일", True), ("만기일은 언제인가요?", "만기일", True), ("원금을 입력해 주세요", "원금", True),
    ("기본금리는 얼마였나요?", "기본금리", True), ("만기일이 알려지지 않았습니다", "만기일", True),        # 이미 말한 값을 모른다고 하는 것도 표시한다
    ("만기일은 2027-01-01로 계산했습니다", "만기일", False), ("만기일 이후라 중도해지가 아닙니다", "만기일", False),
    # H13 오탐: 기본금리를 묻는 안내 문장이 '신규일'을 또 묻는 것으로 읽히면 안 된다
    ("신규일부터 6개월이 지나 6개월 이상 구간이라, 중도해지금리를 계산하려면 기본금리가 필요합니다", "신규일", False),
    ("신규일 당시 홈페이지에 고시된 계약기간별 기본금리(연 %)는 얼마였나요?", "신규일", False),
    ("신규일 당시 홈페이지에 고시된 계약기간별 기본금리(연 %)는 얼마였나요?", "기본금리", False),
])
def test_asks_for_only_when_the_label_is_what_is_asked_for(sentence, label, expected):
    assert heldout_run.asks_for(sentence, label) is expected


def test_unknown_argument_and_after_tax_promise_flags():
    tr = [trace("calc_interest", "invalid_input", args={"termination_date": "2026-10-28", "filled_args": ["termination_date"], "note": "x"})]
    flags = heldout_run.grade(q("H20"), {**result("세후 이자를 알려드리겠습니다.", tr), "today": "2026-10-28"})["flags"]
    assert "unknown_arg:filled_args" in flags and "unknown_arg:note" in flags and "promises_after_tax" in flags
    clean = heldout_run.grade(q("H20"), {**result("세후 이자는 계산할 수 없고 세전 이자만 알려드릴 수 있어요.", [trace("calc_interest", "missing_info", args={"termination_date": "2026-10-28"})]), "today": "2026-10-28"})["flags"]
    assert clean == []


def test_run_log_shows_what_the_code_did_for_the_model():
    class A:
        def ask(self, question, session_id, today):
            return result("값을 알려주세요.", [{**trace("calc_interest", "missing_info", args={"principal": 1}, dropped={"base_rate": 3.5}),
                                            "filled": {"maturity_date": "2026-06-01"}, "ignored": ["filled_args"], "forced": True}])
    lines = []
    heldout_run.run(agent=A(), only={"H1"}, log=lines.append)
    text = "\n".join(lines)
    assert "[말하지 않은 값 제외 base_rate]" in text and "[질문에서 채움 maturity_date]" in text and "[무시한 인자 filled_args]" in text and "[코드가 먼저 검색]" in text
