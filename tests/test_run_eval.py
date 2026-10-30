"""평가 실행기(eval/run_eval.py) 시험 (10일차): 문항 고르기, 실행 · 채점 · 요약, 저장 · 재채점, 검증용 규칙(고정이 깨졌거나 이미 실행했으면 거부)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import heldout_tools as ht  # noqa: E402
from eval import run_eval as re_  # noqa: E402

TODAY = "2026-10-28"


def row(i, category, behavior, core=False, split="dev", question=None, **gold):
    return {"id": i, "split": split, "core": core, "source": [f"t:{i}"], "category": category, "question": question or f"{i} 질문", "today": TODAY,
            "gold": {"behavior": behavior, "tools": gold.pop("tools", []), "tools_allowed": gold.pop("allowed", []), **gold}, "tags": [], "note": ""}


QUESTIONS = [
    row("Q001", "rule", "search", core=True, tools=["search_terms"], chunks=["A"]),
    row("Q002", "rule", "search", tools=["search_terms"]),
    row("Q003", "missing", "ask_missing", core=True, allowed=["calc_interest"], missing=["신규일"], calc={"args": {"principal": 10_000_000}, "status": "missing_info"}),
    row("Q004", "scope", "no_tool", core=True),
    row("Q005", "rule", "search", tools=["search_terms"]),
]


def t(name, status="ok", **kw):
    return {"name": name, "args": kw.pop("args", {}), "status": status, "ms": 5, "forced": False, "forced_kind": None, "retried": False, "duplicate": False, "dropped": {},
            "filled": {}, "ignored": [], **kw}


class FakeAgent:
    """질문 id(앞 4글자)에 따라 정해진 결과를 돌려준다."""

    def __init__(self, behave=None):
        self.calls, self.behave = [], behave or {}

    def ask(self, question, session_id, today=None):
        self.calls.append((question, session_id, today))
        qid = question[:4]
        if qid in self.behave:
            return self.behave[qid](question) if callable(self.behave[qid]) else self.behave[qid]
        good = {"Q001": ("약관상 가능해요.", [t("search_terms")], [{"kind": "약관", "chunk_id": "A"}]), "Q002": ("약관상 가능해요.", [t("search_terms")], [{"kind": "약관", "chunk_id": "B"}]),
                "Q003": ("신규일을 알려주세요.", [t("calc_interest", "missing_info", args={"principal": 10_000_000})], []), "Q004": ("안녕하세요!", [], []),
                "Q005": ("약관상 가능해요.", [t("search_terms")], [{"kind": "약관", "chunk_id": "A"}])}
        ans, trace, evidence = good.get(qid, ("답변", [], []))
        return {"answer": ans, "error": None, "stop_reason": None, "tool_calls": len(trace), "model_calls": 2, "ms": 1500, "trace": trace, "evidence": evidence, "evidence_text": ""}


@pytest.fixture
def qfile(tmp_path, monkeypatch):
    path = tmp_path / "questions.jsonl"
    path.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in QUESTIONS), encoding="utf-8")
    monkeypatch.setattr(re_, "QUESTIONS", path)
    monkeypatch.setattr(ht, "QUESTIONS", path)
    monkeypatch.setattr(ht, "MANIFEST", tmp_path / "manifest.json")
    return path


# ---------- 고르기 ----------
def ids(qs):
    return [q["id"] for q in qs]


def test_select_filters_by_split_core_category_and_id_in_file_order():
    qs = QUESTIONS + [row("X001", "rule", "search", split="held_out", tools=["search_terms"])]
    assert ids(re_.select(qs)) == ["Q001", "Q002", "Q003", "Q004", "Q005"]                    # 기본은 개발용만
    assert ids(re_.select(qs, split="held_out")) == ["X001"] and len(re_.select(qs, split="all")) == 6
    assert ids(re_.select(qs, core=True)) == ["Q001", "Q003", "Q004"]
    assert ids(re_.select(qs, category="rule")) == ["Q001", "Q002", "Q005"]
    assert ids(re_.select(qs, only={"Q005", "Q001"})) == ["Q001", "Q005"]
    assert ids(re_.select(qs, core=True, category="rule")) == ["Q001"] and ids(re_.select(qs, limit=2)) == ["Q001", "Q002"]


def test_select_sample_is_reproducible_keeps_file_order_and_never_asks_for_more_than_exists():
    a, b = re_.select(QUESTIONS, sample=3, seed=1), re_.select(QUESTIONS, sample=3, seed=1)
    assert ids(a) == ids(b) and len(a) == 3 and ids(a) == sorted(ids(a))
    assert any(ids(re_.select(QUESTIONS, sample=3, seed=s)) != ids(a) for s in range(2, 12))
    assert len(re_.select(QUESTIONS, sample=99)) == 5


def test_compact_keeps_only_the_needed_fields_and_trims_the_answer():
    full = {"answer": "가" * 500, "error": None, "stop_reason": None, "tool_calls": 1, "model_calls": 2, "ms": 9, "trace": [], "evidence": [], "evidence_text": "", "extra": "버림"}
    c = re_.compact(full)
    assert set(c) == set(re_.RES_KEYS) and c["answer"] == "가" * 300 and re_.compact(full, full=True)["answer"] == "가" * 500
    assert re_.compact({"answer": None})["answer"] is None


# ---------- 실행 ----------
def test_run_asks_each_question_in_a_fresh_session_with_the_fixed_date_and_grades_it():
    agent, lines = FakeAgent(), []
    rows = re_.run(QUESTIONS[:3], agent, log=lines.append)
    assert agent.calls == [("Q001 질문", "eval-Q001", TODAY), ("Q002 질문", "eval-Q002", TODAY), ("Q003 질문", "eval-Q003", TODAY)]
    assert [r["id"] for r in rows] == ["Q001", "Q002", "Q003"] and all(r["grade"]["clean"] for r in rows)
    assert rows[0]["grade"]["gold_chunk_in_evidence"] is True and rows[1]["category"] == "rule"
    assert lines[0].startswith("[OK] Q001") and "search_terms→ok" in lines[0]


def test_a_failing_question_is_marked_and_the_run_continues():
    def boom(question):
        raise RuntimeError("ollama off")
    rows, lines = re_.run(QUESTIONS[:3], FakeAgent({"Q002": boom}), log=(lines := []).append), lines
    assert [r["grade"]["clean"] for r in rows] == [True, False, True]
    bad = rows[1]
    assert bad["res"]["stop_reason"] == "crash" and "RuntimeError: ollama off" in bad["res"]["error"] and "model_error" in bad["grade"]["flags"]
    assert lines[1].startswith("[NG] Q002")


def test_forced_calls_are_marked_with_a_star_in_the_log():
    lines = []
    forced = {"Q001": {"answer": "약관상 가능해요.", "error": None, "stop_reason": None, "model_calls": 2, "ms": 1, "tool_calls": 1,
                       "trace": [t("search_terms", forced=True, forced_kind="search")], "evidence": [{"kind": "약관", "chunk_id": "A"}], "evidence_text": ""}}
    re_.run(QUESTIONS[:1], FakeAgent(forced), log=lines.append)
    assert "search_terms→ok*" in lines[0]


# ---------- 요약 · 저장 · 재채점 ----------
def test_the_summary_table_has_overall_and_per_category_rows_in_text_and_markdown():
    rows = re_.run(QUESTIONS, FakeAgent(), log=lambda s: None)
    summary = re_.summarize(rows)
    text, md = re_.format_summary(summary), re_.format_summary(summary, markdown=True)
    assert text.splitlines()[0].startswith("분류") and "전체" in text and "rule" in text and "missing" in text and "5/5" in text and "표시(flags): 없음" in text
    assert md.splitlines()[0].startswith("| 분류 |") and md.splitlines()[1].startswith("| ---") and "| 전체 | 5 | 5/5 | 5/5 |" in md
    assert "p50ms" in text and "1500" in text and "모델호출" in text


def test_summary_shows_dashes_for_columns_that_do_not_apply_and_lists_the_flags():
    rows = re_.run(QUESTIONS[3:4], FakeAgent({"Q004": {"answer": "고객센터 1544-1111", "error": None, "stop_reason": None, "model_calls": 1, "ms": 5, "tool_calls": 0, "trace": [],
                                                       "evidence": [], "evidence_text": ""}}), log=lambda s: None)
    out = re_.format_summary(re_.summarize(rows))
    assert "phone_in_answer 1" in out and "0/1" in out


def test_saved_results_can_be_rescored_without_the_model_and_a_changed_question_is_skipped(tmp_path):
    rows = re_.run(QUESTIONS, FakeAgent(), log=lambda s: None)
    path = tmp_path / "runs" / "a.json"
    re_.save(str(path), rows, re_.summarize(rows), {"n": 5})
    assert json.loads(path.read_text(encoding="utf-8"))["meta"] == {"n": 5}
    same, info = re_.rescore(str(path), QUESTIONS)
    assert [r["grade"]["clean"] for r in same] == [True] * 5 and info["skipped"] == []
    stricter = [dict(q) for q in QUESTIONS]
    stricter[0] = {**stricter[0], "gold": {**stricter[0]["gold"], "must_say_any": ["절대 없는 말"]}}
    stricter[1] = {**stricter[1], "question": "Q002 바뀐 질문"}
    again, info = re_.rescore(str(path), stricter)
    assert info["skipped"] == ["Q002"] and again[0]["grade"]["clean"] is False and len(again) == 4


# ---------- main ----------
def test_main_runs_the_selected_questions_prints_the_table_and_saves(qfile, tmp_path, capsys):
    agent, out = FakeAgent(), tmp_path / "r.json"
    assert re_.main(["--core", "--save", str(out)], agent=agent) == 0
    printed = capsys.readouterr().out
    assert [c[1] for c in agent.calls] == ["eval-Q001", "eval-Q003", "eval-Q004"] and "전체" in printed and f"저장: {out}" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["meta"]["split"] == "dev" and saved["meta"]["core"] is True and saved["meta"]["n"] == 3 and len(saved["rows"]) == 3 and saved["summary"]["overall"]["n"] == 3


def test_main_with_category_only_limit_and_markdown(qfile, capsys):
    agent = FakeAgent()
    assert re_.main(["--category", "rule", "--only", "Q002,Q005", "--md"], agent=agent) == 0
    assert [c[1] for c in agent.calls] == ["eval-Q002", "eval-Q005"] and "| 분류 |" in capsys.readouterr().out
    assert re_.main(["--limit", "1"], agent=(a := FakeAgent())) == 0 and len(a.calls) == 1


def test_main_says_so_when_nothing_is_selected(qfile, capsys):
    assert re_.main(["--category", "handoff"], agent=FakeAgent()) == 1 and "선택된 문항이 없어요" in capsys.readouterr().out
    assert re_.main(["--split", "held_out"], agent=FakeAgent()) == 1 and "heldout_tools.py" in capsys.readouterr().out


def test_main_rescores_a_saved_file(qfile, tmp_path, capsys):
    out = tmp_path / "r.json"
    re_.main(["--save", str(out)], agent=FakeAgent())
    capsys.readouterr()
    assert re_.main(["--rescore", str(out)]) == 0 and "다시 채점: 5문항" in capsys.readouterr().out


def test_main_measures_retrieval_separately_without_the_model(qfile, capsys):
    ranked = {"Q001 질문": ["A", "x"]}                                                     # Q001만 정답 청크(A)가 있다
    assert re_.main(["--retrieval", "--k", "2"], search_fn=lambda query, k: {"status": "ok", "results": [{"chunk_id": c} for c in ranked.get(query, [])][:k]}) == 0
    out = capsys.readouterr().out
    assert "정답 청크가 있는 1문항, 상위 2개" in out and "Recall@2: 1.000" in out and "MRR: 1.000" in out and "못 찾은 문항: 없음" in out


# ---------- 검증용 규칙 ----------
def frozen_held_out(qfile):
    ht.intake("시스템을 모르는 사람의 질문 하나\n질문 둘", "친구 A")
    rows = ht.load_rows()
    for r in rows:
        if r["split"] == "held_out":
            r.update(category="rule", gold={"behavior": "search", "tools": ["search_terms"], "tools_allowed": []})
    ht.save_rows(rows)
    ht.freeze("Claude")


class HeldOutAgent(FakeAgent):
    def ask(self, question, session_id, today=None):
        self.calls.append((question, session_id, today))
        return {"answer": "약관상 가능해요.", "error": None, "stop_reason": None, "model_calls": 2, "ms": 10, "tool_calls": 1, "trace": [t("search_terms")],
                "evidence": [{"kind": "약관", "chunk_id": "A"}], "evidence_text": ""}


def test_held_out_cannot_run_before_it_is_frozen(qfile, capsys):
    ht.intake("질문 하나", "친구")
    agent = HeldOutAgent()
    assert re_.main(["--split", "held_out", "--save", "x.json"], agent=agent) == 2 and "고정되지 않았습니다" in capsys.readouterr().out and agent.calls == []


def test_held_out_needs_a_saved_result_and_may_run_once_per_milestone(qfile, tmp_path, capsys):
    frozen_held_out(qfile)
    agent = HeldOutAgent()
    assert re_.main(["--split", "held_out"], agent=agent) == 2 and "--save" in capsys.readouterr().out and agent.calls == []        # 저장 없이는 실행하지 않는다
    out = tmp_path / "held.json"
    assert re_.main(["--split", "held_out", "--save", str(out)], agent=agent) == 0 and len(agent.calls) == 2
    assert [r["file"] for r in ht.runs()] == [str(out)] and ht.runs()[0]["behavior_ok"] == {"hit": 2, "n": 2}
    capsys.readouterr()
    again = HeldOutAgent()
    assert re_.main(["--split", "held_out", "--save", str(out)], agent=again) == 2 and "한 번만" in capsys.readouterr().out and again.calls == []
    assert re_.main(["--split", "held_out", "--save", str(out), "--allow-repeat"], agent=again) == 0 and len(ht.runs()) == 2          # 예외는 기록에 남는다


def test_held_out_refuses_to_run_when_a_frozen_question_was_changed(qfile, capsys):
    frozen_held_out(qfile)
    rows = ht.load_rows()
    rows[-1]["gold"]["behavior"] = "scope"                                                   # 결과를 보고 정답을 바꾸려는 시도
    ht.save_rows(rows)
    agent = HeldOutAgent()
    assert re_.main(["--split", "held_out", "--save", "x.json"], agent=agent) == 2
    assert "고정 뒤에 문항 또는 정답이 바뀌었습니다" in capsys.readouterr().out and agent.calls == [] and ht.runs() == []


def test_a_mixed_selection_that_contains_held_out_questions_follows_the_held_out_rules(qfile, capsys):
    frozen_held_out(qfile)
    agent = HeldOutAgent()
    assert re_.main(["--split", "all"], agent=agent) == 2 and agent.calls == []              # dev와 섞어도 검증용 규칙(저장 필수)이 적용된다


def test_dev_runs_are_not_limited_and_never_touch_the_manifest(qfile, tmp_path):
    frozen_held_out(qfile)
    for _ in range(3):
        assert re_.main(["--core"], agent=FakeAgent()) == 0
    assert ht.runs() == []


# ---------- 진행 표시: 멈춘 건지 느린 건지 보이게 ----------
def test_progress_is_reported_before_each_question_starts_so_a_hang_is_visible():
    order = []

    class Spy(FakeAgent):
        def ask(self, question, session_id, today=None):
            order.append(("ask", session_id))
            return super().ask(question, session_id, today)
    re_.run(QUESTIONS[:2], Spy(), log=lambda s: order.append(("log", s[:8])), progress=lambda i, n, q: order.append(("progress", f"{i}/{n} {q['id']}")))
    assert order[:3] == [("progress", "1/2 Q001"), ("ask", "eval-Q001"), ("log", "[OK] Q00")]            # 질문을 시작하기 전에 먼저 알린다
    assert order[3] == ("progress", "2/2 Q002")


def test_the_default_progress_goes_to_stderr_and_the_result_line_shows_the_seconds(capsys):
    lines = []
    re_.run(QUESTIONS[:2], FakeAgent(), log=lines.append)
    err = capsys.readouterr().err
    assert "[1/2] Q001 실행 중" in err and "[2/2] Q002 실행 중" in err and capsys.readouterr().out == ""
    assert lines[0].split("\n")[0].endswith("초)") and "(0초)" in lines[0]
