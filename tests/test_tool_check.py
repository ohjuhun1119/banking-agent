"""5일차 채점기(eval/tool_check.py) 시험: 정답 모델은 전부 통과하고, 틀린 출력은 오류 종류대로 잡혀야 한다."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from langchain_core.messages import AIMessage  # noqa: E402

import tool_cases  # noqa: E402
import tool_check as chk  # noqa: E402

BY_ID = {c["id"]: c for c in tool_cases.CASES}


def ideal_calls(case):
    exp = case["expect"]
    if exp["tool"] is None:
        return []
    if exp["tool"] == "search_terms":
        return [("search_terms", {"query": case["question"]})]
    return [("calc_interest", dict(exp["args"] if exp["strict"] else exp["allowed"]))]


class ScriptModel:
    """질문 -> 도구 호출 목록을 함수로 정하는 가짜 모델."""

    def __init__(self, fn):
        self.fn, self.n = fn, 0

    def invoke(self, messages):
        self.n += 1
        q = messages[-1].content
        return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(self.fn(q, self.n))])


ORACLE = ScriptModel(lambda q, n: ideal_calls(next(c for c in tool_cases.CASES if c["question"] == q)))


def g(case_id, calls, invalid=()):
    return chk.grade(BY_ID[case_id], {"tool_calls": [{"name": n, "args": a} for n, a in calls], "invalid_calls": list(invalid)})


def codes(res):
    return sorted(e["code"] + (f"[{e['field']}]" if e.get("field") else "") for e in res["errors"])


def test_case_set_shape():
    groups = [c["group"] for c in tool_cases.CASES]
    assert len(tool_cases.CASES) == 25 and len({c["id"] for c in tool_cases.CASES}) == 25
    assert {k: groups.count(k) for k in set(groups)} == {"약관": 8, "계산": 8, "부족": 5, "엣지": 1, "불필요": 3}


def test_strict_calc_cases_match_the_calculator():
    """계산 질문의 기대값이 실제로 계산 도구에서 의도한 상태가 나오는 값인지 (기대값 자체의 오타 방지)."""
    from app.tools.interest import calc_interest
    want = {"C1": "ok", "C2": "ok", "C3": "ok", "C4": "ok", "C5": "ok", "C6": "ok", "C7": "ok", "C8": "need_base_rate",
            "E1": "invalid_input"}
    for cid, status in want.items():
        assert calc_interest(**BY_ID[cid]["expect"]["args"])["status"] == status, cid
    assert calc_interest(**BY_ID["C4"]["expect"]["args"])["interest_krw"] == 63013


def test_oracle_passes_everything():
    res = chk.run("detailed", model=ORACLE, log=None)
    s = res["summary"]
    assert s["pass"] == s["total"] == 25 and s["selection_ok"] == 25 and s["args_ok"] == 25 and s["error_codes"] == {}


@pytest.mark.parametrize("cid,calls,expected", [
    ("T1", [], ["missing_call"]),
    ("T1", [("calc_interest", {})], ["extra_call", "missing_call"]),
    ("T1", [("search_terms", {"query": " "})], ["empty_query"]),
    ("T1", [("search_terms", {"query": "중도해지"}), ("calc_interest", {})], ["extra_call"]),
    ("N1", [("search_terms", {"query": "인사"})], ["extra_call"]),
    ("N1", [("get_weather", {})], ["unknown_tool"]),
    ("C1", [], ["missing_call"]),
    ("C1", [("calc_interest", {"principal": "1000만원", "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-01-21"})], ["unparsable_principal[principal]"]),
    ("C1", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "20일"})], ["date_not_iso[termination_date]"]),
    ("C1", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-01-22"})], ["wrong_termination_date[termination_date]"]),
    ("C1", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-01-21", "base_rate": 3.5})], ["extra_arg[base_rate]"]),
    ("C4", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-07-20"})], ["missing_arg[base_rate]"]),
    ("C4", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-07-20", "base_rate": 0.035})], ["wrong_base_rate[base_rate]"]),
    ("C1", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-01-21", "days": 20})], ["extra_arg[days]"]),
    ("M3", [("calc_interest", {"principal": 5000000, "start_date": "2026-06-01", "termination_date": "2026-10-28"})],
     ["extra_arg[start_date]"]),
    ("M4", [("calc_interest", {"principal": 20000000, "maturity_date": "2027-05-28"})], ["extra_arg[maturity_date]"]),
    ("M5", [("calc_interest", {"principal": 10000000, "start_date": "2026-02-01"})], ["extra_arg[principal]"]),
    ("M1", [("calc_interest", {"termination_date": "오늘"})], ["extra_arg[termination_date]"]),
    ("M2", [("calc_interest", {"principal": "1000만원"})], ["unparsable_principal[principal]"]),
])
def test_errors_are_caught(cid, calls, expected):
    res = g(cid, calls)
    assert not res["pass"] and codes(res) == sorted(expected)


@pytest.mark.parametrize("cid,calls", [
    ("M1", []),                                                               # 되묻기
    ("M1", [("calc_interest", {})]),                                          # 아는 값 없이 호출
    ("M2", [("calc_interest", {"principal": "10,000,000원"})]),               # 도구가 받아들이는 형식이면 통과
    ("M3", [("calc_interest", {"principal": 5000000, "termination_date": "2026-10-28", "start_date": ""})]),  # 빈 값은 안 넣은 것
    ("M5", [("search_terms", {"query": "중도해지"})]),                         # 되물으면서 규칙 검색은 허용
    ("C5", [("calc_interest", {"principal": 10000000, "start_date": "2026-01-01", "maturity_date": "2028-01-01",
                               "termination_date": "2026-10-28", "base_rate": "4.0%"})]),
    ("C1", [("search_terms", {"query": "중도해지 금리"}),
            ("calc_interest", {"principal": "10000000", "start_date": "2026-01-01", "maturity_date": "2027-01-01",
                               "termination_date": "2026-01-21", "base_rate": None})]),
])
def test_acceptable_outputs_pass(cid, calls):
    res = g(cid, calls)
    assert res["pass"], res


def test_invalid_call_counts_as_selection_error():
    res = g("T1", [("search_terms", {"query": "중도해지"})], invalid=[{"name": "search_terms", "args": "{bad", "error": "json"}])
    assert codes(res) == ["invalid_call"]


def test_summary_separates_selection_and_args():
    def fn(q, n):
        case = next(c for c in tool_cases.CASES if c["question"] == q)
        if case["id"] == "T1":
            return []                                                     # 도구 선택 오류
        if case["id"] == "C1":
            return [("calc_interest", {**case["expect"]["args"], "principal": "1000만원"})]   # 인자 오류
        return ideal_calls(case)
    s = chk.run("short", model=ScriptModel(fn), log=None)["summary"]
    assert (s["pass"], s["selection_ok"], s["args_ok"]) == (23, 24, 23)
    assert s["error_codes"] == {"missing_call": 1, "unparsable_principal": 1}
    assert s["by_group"]["약관"] == "7/8" and s["by_group"]["계산"] == "7/8"


def test_repeat_finds_flaky_questions():
    def fn(q, n):
        case = next(c for c in tool_cases.CASES if c["question"] == q)
        if case["id"] == "N2" and n > 25:                                     # 두 번째 반복에서만 틀림
            return [("search_terms", {"query": q})]
        return ideal_calls(case)
    res = chk.run("detailed", model=ScriptModel(fn), repeat=2, log=None)
    assert res["summary"]["total"] == 50 and res["summary"]["flaky"] == ["N2"]


def test_only_selects_cases_and_rejects_unknown():
    assert [c["id"] for c in chk._select_cases("C1, T2")] == ["C1", "T2"]
    with pytest.raises(SystemExit):
        chk._select_cases("C1,Z9")


def test_save_and_compare(tmp_path):
    good = chk.run("detailed", model=ORACLE, log=None)
    bad = chk.run("short", model=ScriptModel(lambda q, n: []), log=None)
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    chk._save(good, str(p1), log=lambda *_: None)
    chk._save(bad, str(p2), log=lambda *_: None)
    lines = []
    out = chk.compare([str(p2), str(p1)], log=lines.append)
    assert out["variants"] == ["short", "detailed"]
    assert out["diff"]["T1"] == ["0", "1"] and "N1" not in out["diff"]       # 도구를 안 부르면 불필요 질문은 둘 다 통과
    assert json.loads(p1.read_text(encoding="utf-8"))["summary"]["pass"] == 25


def test_retrieval_compares_question_and_model_query():
    pytest.importorskip("chromadb")
    import check_search  # noqa: F401  (rag 경로는 run_retrieval이 추가한다)

    def search_fn(query, product=None, k=5, max_distance=None):
        assert product == "하나의 정기예금"
        hit = "hana-deposit-sheet__early_termination"
        ids = [hit, "x2", "x3"] if "모델" in query else ["x1", "x2", hit]
        return {"status": "ok", "results": [{"rank": i, "chunk_id": c} for i, c in enumerate(ids, start=1)]}

    def fn(q, n):
        return [] if "날씨" in q or n % 7 == 0 else [("search_terms", {"query": "모델 검색어"})]
    res = chk.run_retrieval("detailed", model=ScriptModel(fn), search_fn=search_fn, log=None)
    s = res["summary"]
    assert s["question"]["n"] == 30 and s["model_query"]["n"] == 30
    et_rows = [r for r in res["rows"] if r["base_rank"] is not None]
    assert all(r["base_rank"] == 3 for r in et_rows)
    assert all(r["model_rank"] == 1 for r in et_rows if r["model_query"])          # 모델 검색어로 검색한 결과로 순위를 매긴다
    assert s["model_query"]["top1"] == sum(1 for r in et_rows if r["model_query"]) and s["question"]["top1"] == 0
    assert s["no_search"] == sum(1 for r in res["rows"] if r["model_query"] is None) > 0
    assert all(r["model_rank"] is None for r in res["rows"] if r["model_query"] is None)   # 검색 안 하면 실패로 센다
