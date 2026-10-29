"""요청 트레이스(app/traces.py) 시험: 도구 선택 과정을 저장하고, 잘못 고른 사례 후보를 신호로 표시한다."""
import json
import sqlite3
from datetime import datetime

import pytest

from app import traces

NOW = datetime(2026, 10, 29, 12, 0, 0)
RULE_Q = "예금 만기일이 지나도 안 찾았는데 지금 해지하면 이자는 어떻게 계산돼요?"          # 검증용 H14
AMOUNT_Q = "만기 한 달 전인데 지금 해지하는 거랑 만기까지 기다리는 거랑 이자 차이가 많이 나나요?"      # H19


def t(name, status="ok", **kw):
    return {"name": name, "args": kw.pop("args", {}), "status": status, "ms": 5, "forced": False, "forced_kind": None, "retried": False, "duplicate": False,
            "dropped": {}, "filled": {}, "ignored": [], **kw}


def result(trace=(), **kw):
    return {"session_id": "s", "answer": "답변", "error": None, "stop_reason": None, "tool_calls": len(trace), "model_calls": 1, "ms": 10, "trace": list(trace),
            "evidence": [], **kw}


# ---------- 신호 ----------
def test_the_real_h14_case_is_flagged_as_a_calculation_call_on_a_rule_question():
    """after_evidence 실제 기록: 모델이 값 없이 계산 도구를 불러 네 값이 모두 걸러졌고 해지일(지금)만 채워졌다."""
    call = t("calc_interest", "missing_info", args={"maturity_date": "2026-10-31", "termination_date": "2026-11-28", "principal": 10_000_000, "start_date": "2026-01-01"},
             dropped={"maturity_date": "x", "termination_date": "x", "principal": 1, "start_date": "x"}, filled={"termination_date": "2026-10-28"})
    s = traces.signals_of(result([call]), RULE_Q)
    assert "calc_on_rule_question" in s and "dropped:maturity_date,principal,start_date,termination_date" in s and "filled:termination_date" in s


def test_a_calculation_with_readable_values_on_an_amount_question_has_no_signals():
    call = t("calc_interest", "ok", args={"principal": 10_000_000, "termination_date": "2026-12-07"})
    assert traces.signals_of(result([call]), "1000만원 이자 얼마예요?") == []
    assert "calc_on_rule_question" not in traces.signals_of(result([t("calc_interest", "ok", args={"principal": 10_000_000, "termination_date": "2026-12-07"})]), RULE_Q)


@pytest.mark.parametrize("kind,expected", [("search", "forced_search"), ("calc", "forced_calc")])
def test_forced_calls_mean_the_model_skipped_the_tool(kind, expected):
    assert expected in traces.signals_of(result([t("search_terms", forced=True, forced_kind=kind)]), "질문")


def test_a_forced_handoff_is_not_a_wrong_tool_signal():
    assert traces.signals_of(result([t("request_handoff", "saved", forced=True, forced_kind="handoff")]), "상담원 연결해줘") == []


def test_other_signals():
    assert "search_on_amount_question" in traces.signals_of(result([t("search_terms")]), AMOUNT_Q)
    assert "search_on_amount_question" not in traces.signals_of(result([t("search_terms"), t("calc_interest", "missing_info")]), AMOUNT_Q)
    s = traces.signals_of(result([t("search_terms", "error", retried=True), t("calc_interest", "invalid_input", ignored=["filled_args"]),
                                  t("search_terms", "duplicate", duplicate=True), t("request_handoff", "needs_confirmation")], stop_reason="tool_limit", error="x"), "질문")
    for sig in ("tool_error", "retried", "invalid_input", "ignored:filled_args", "duplicate_call", "handoff_confirmation", "tool_limit", "error"):
        assert sig in s
    assert len(s) == len(set(s))                                              # 중복 없이


def test_filled_values_of_a_forced_call_are_not_a_model_omission():
    forced = t("calc_interest", "missing_info", forced=True, forced_kind="calc", filled={"termination_date": "2026-10-28"})
    assert not any(x.startswith("filled") for x in traces.signals_of(result([forced]), AMOUNT_Q))
    omitted = t("calc_interest", "not_early_termination", filled={"maturity_date": "2026-06-01"}, args={"principal": 1})
    assert "filled:maturity_date" in traces.signals_of(result([omitted]), "1000만원 이자 얼마예요?")


# ---------- 저장 · 조회 · 라벨 · 내보내기 ----------
def test_record_stores_the_selection_process_without_any_document_text(tmp_path):
    db = tmp_path / "t.sqlite3"
    tid = traces.record(result([t("search_terms", args={"query": "일부해지"}, forced=True, forced_kind="search")], evidence=[{"kind": "약관"}, {"kind": "약관"}], answer="약관상 가능해요"),
                        "일부만 해지할 수 있어요?", db, NOW)
    (row,) = traces.list_traces(db_path=db)
    assert tid == row["id"] == 1 and row["created_at"] == "2026-10-29T12:00:00" and row["question"] == "일부만 해지할 수 있어요?" and row["answer"] == "약관상 가능해요"
    assert row["tools"][0]["name"] == "search_terms" and row["tools"][0]["args"] == {"query": "일부해지"} and row["tools"][0]["forced_kind"] == "search"
    assert row["evidence_kinds"] == "약관,약관" and row["signals"] == ["forced_search"] and row["label"] is None
    cols = [c[1] for c in sqlite3.connect(db).execute("PRAGMA table_info(traces)")]
    assert "text" not in cols and "results" not in cols                       # 검색 결과 본문을 저장하는 칸이 없다


def test_record_never_raises_and_can_be_switched_off(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    assert traces.record(result(), "q", folder, NOW) is None                   # 저장 실패: 요청을 막지 않는다
    assert traces.record({}, "q", tmp_path / "x.sqlite3", NOW) is not None     # 빈 결과도 저장은 된다
    monkeypatch.setenv("TRACE_LOG", "0")
    assert not traces.enabled() and traces.record(result(), "q", tmp_path / "y.sqlite3", NOW) is None and not (tmp_path / "y.sqlite3").exists()


def test_list_filters_and_orders_newest_first(tmp_path):
    db = tmp_path / "t.sqlite3"
    traces.record(result(), "첫째", db, NOW)
    traces.record(result([t("search_terms", forced=True, forced_kind="search")]), "둘째", db, NOW)
    traces.record(result(), "셋째", db, NOW)
    assert [r["question"] for r in traces.list_traces(db_path=db)] == ["셋째", "둘째", "첫째"]
    assert [r["question"] for r in traces.list_traces(only_signals=True, db_path=db)] == ["둘째"]
    assert [r["question"] for r in traces.list_traces(limit=1, db_path=db)] == ["셋째"] and traces.list_traces(db_path=tmp_path / "none") == []


def test_mark_accepts_only_known_labels_and_existing_traces(tmp_path):
    db = tmp_path / "t.sqlite3"
    traces.record(result(), "q", db, NOW)
    assert traces.mark(1, "wrong_tool", "방법 질문에 계산 도구", db) and traces.list_traces(db_path=db)[0]["label"] == "wrong_tool"
    assert traces.list_traces(db_path=db)[0]["note"] == "방법 질문에 계산 도구"
    assert not traces.mark(1, "bogus", db_path=db) and not traces.mark(99, "ok", db_path=db) and not traces.mark(1, "ok", db_path=tmp_path / "none")
    assert traces.list_traces(label="ok", db_path=db) == [] and len(traces.list_traces(label="wrong_tool", db_path=db)) == 1


def test_export_lists_only_the_labelled_cases_and_cuts_long_answers(tmp_path):
    db = tmp_path / "t.sqlite3"
    call = t("calc_interest", "missing_info", args={"principal": 10_000_000}, dropped={"principal": 1}, filled={"termination_date": "x"})
    traces.record(result([call], answer="가" * 500), RULE_Q, db, NOW)
    traces.record(result(answer="무관"), "다른 질문", db, NOW)
    traces.mark(1, "wrong_tool", "H14 유형", db)
    md = traces.export_markdown("wrong_tool", db)
    assert md.startswith("# 도구 선택 사례 (wrong_tool, 1건)") and RULE_Q in md and "다른 질문" not in md
    assert "calc_interest(principal=10000000) → missing_info [뺀 값 principal] [채운 값 termination_date]" in md and "메모: H14 유형" in md
    assert "가" * traces.EXCERPT_CHARS in md and "가" * (traces.EXCERPT_CHARS + 1) not in md


def test_the_command_line_lists_marks_and_exports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(traces, "DB_PATH", tmp_path / "c.sqlite3")
    traces.record(result([t("search_terms", forced=True, forced_kind="search")]), "일부만 해지할 수 있어요?", now=NOW)
    traces.main(["list", "--signals"])
    assert "일부만 해지할 수 있어요?" in capsys.readouterr().out
    traces.main(["mark", "1", "wrong_tool", "--note", "검색 생략"])
    assert "라벨을 붙였어요" in capsys.readouterr().out
    traces.main(["export"])
    assert "검색 생략" in capsys.readouterr().out
    traces.main(["mark", "9", "ok"])
    assert "붙이지 못했어요" in capsys.readouterr().out


def test_a_call_the_code_forced_is_never_blamed_on_the_model_even_on_a_rule_question():
    forced = t("calc_interest", "missing_info", forced=True, forced_kind="calc", filled={"termination_date": "2026-10-28"})
    assert "calc_on_rule_question" not in traces.signals_of(result([forced]), RULE_Q)                       # 같은 호출을 모델이 했다면 표시되는 경우
    own = t("calc_interest", "missing_info", filled={"termination_date": "2026-10-28"})
    assert "calc_on_rule_question" in traces.signals_of(result([own]), RULE_Q)


def test_the_same_signal_from_several_calls_is_listed_once():
    twice = [t("search_terms", forced=True, forced_kind="search"), t("search_terms", forced=True, forced_kind="search"),
             t("calc_interest", "missing_info", dropped={"principal": 1}), t("calc_interest", "missing_info", dropped={"principal": 2})]
    s = traces.signals_of(result(twice), AMOUNT_Q)                                  # 금액 질문이라 다른 신호는 붙지 않는다
    assert s == ["forced_search", "dropped:principal"]
