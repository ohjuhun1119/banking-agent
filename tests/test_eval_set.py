"""평가 문항(eval/questions.jsonl) 시험 (10일차): 형식, 중복, 시나리오 범위, 그리고 정답 금액을 계산 도구와 별개의 공식으로 다시 검산한다."""
import json
import math
import re
import sys
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest
from dateutil.relativedelta import relativedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import build_questions as bq  # noqa: E402

ROWS = [json.loads(line) for line in (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
DEV = [r for r in ROWS if r["split"] == "dev"]
LABELS = {"principal": "원금", "start_date": "신규일", "maturity_date": "만기일", "termination_date": "해지일"}


# ---------- 계산 도구와 별개의 기준 구현 (노션 '중도해지 계산 규칙'만 보고 쓴 것) ----------
def completed_months(start: date, end: date) -> int:
    n = 0
    while start + relativedelta(months=n + 1) <= end:                   # 신규일에 n개월을 더한 날(월말은 말일로 보정)이 해지일 이하인 가장 큰 n
        n += 1
    return n


def reference(args: dict) -> dict:
    s, m, t = (date.fromisoformat(args[k]) for k in ("start_date", "maturity_date", "termination_date"))
    if args["principal"] < 1_000_000 or m > s + relativedelta(years=5) or m < s + relativedelta(months=1) or t < s:
        return {"status": "invalid_input"}
    if t >= m:
        return {"status": "not_early_termination"}
    months, elapsed, contract = completed_months(s, t), (t - s).days, (m - s).days
    if months < 1:
        rate = Fraction(10, 100)
    elif months < 3:
        rate = Fraction(15, 100)
    elif months < 6:
        rate = Fraction(20, 100)
    else:
        if args.get("base_rate") is None:
            return {"status": "need_base_rate", "months": months}
        diff = Fraction(60, 100) if months < 9 else Fraction(70, 100) if months < 11 else Fraction(90, 100)
        raw = Fraction(str(args["base_rate"])) * diff * Fraction(min(elapsed, contract), contract)
        rate = max(Fraction(math.floor(raw * 1000), 1000), Fraction(20, 100))       # 소수점 셋째 자리까지, 넷째 자리 절사 · 최저 0.20%
    interest = math.floor(Fraction(args["principal"]) * rate / 100 * elapsed / 365)
    return {"status": "ok", "months": months, "rate_pct": f"{float(rate):.3f}", "interest_krw": interest}


# ---------- 형식 ----------
def test_the_file_is_what_the_builder_produces():
    assert bq.main(["--check"]) == 0


def test_every_row_has_the_required_shape():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[QX]\d{3}", i) for i in ids)
    for r in ROWS:
        assert r["split"] in ("dev", "held_out") and isinstance(r["core"], bool) and r["today"] == "2026-10-28" and r["question"].strip()
        if r["split"] == "held_out":
            assert r["source"] == ["outsider"] and r.get("writer") and r["core"] is False, r["id"]      # 검증용은 받은 출처와 작성자를 남긴다
            if not r.get("gold"):
                continue                                                 # 정답을 쓰기 전 (heldout_tools가 고정 전에 확인)
        g = r["gold"]
        assert r["category"] in bq.CATEGORIES and g["behavior"] in bq.BEHAVIORS, r["id"]
        assert isinstance(g["tools"], list) and isinstance(g.get("tools_allowed", []), list), r["id"]
        assert set(g["tools"]) <= {"search_terms", "calc_interest", "request_handoff"} and r["source"], r["id"]
        for p in g.get("must_not") or []:
            re.compile(p)
        assert all(isinstance(x, str) and x for x in g.get("must_say_any") or [])


def test_no_two_questions_are_the_same():
    keys = [" ".join(r["question"].split()) for r in ROWS]
    assert len(keys) == len(set(keys))
    sources = [s for r in DEV for s in r["source"]]
    assert len(sources) == len(set(sources))                             # 같은 출처가 두 개발용 문항에 들어가지 않는다 (검증용은 모두 출처가 outsider라 제외)


def test_all_existing_sources_are_accounted_for():
    """5일차 확인용 25 · 6일차 검증용 20 · 3일차 검색 33이 하나도 빠지지 않고(같은 질문은 합쳐서) 들어 있다."""
    src = {s for r in ROWS for s in r["source"]}
    assert {f"tool_check:{x}" for x in ("T1", "T8", "C1", "C8", "M1", "M5", "E1", "N1", "N3")} <= src
    assert {f"heldout:H{i}" for i in range(1, 21)} <= src
    assert {f"retrieval:{i}" for i in range(1, 34)} <= src
    assert len([s for s in src if s.startswith("tool_check:")]) == 25 and len([s for s in src if s.startswith("heldout:")]) == 20


def test_the_six_day_held_out_questions_are_demoted_to_dev_because_they_were_seen():
    h = [r for r in ROWS if any(s.startswith("heldout:") for s in r["source"])]
    assert len(h) == 20 and all(r["split"] == "dev" and "contaminated" in r["tags"] for r in h)


# ---------- 범위: 노션 10일차 '필수 시나리오' ----------
def test_required_scenarios_are_covered():
    ok = [(r, reference(r["gold"]["calc"]["args"])) for r in DEV if r["gold"]["behavior"] == "calc_ok"]
    assert any(ref["months"] < 6 for _, ref in ok), "6개월 미만"
    assert any(r["gold"]["behavior"] == "ask_base_rate" for r in DEV), "6개월 이상(기본금리 되묻기)"
    assert any("적용금리" in r["tags"] for r in DEV), "쿠폰 · 적용금리를 기본금리로 말하는 경우"
    assert sum("hana-deposit-sheet__interest_calc" in (r["gold"].get("traps") or []) and "hana-deposit-sheet__early_termination" in (r["gold"].get("chunks") or []) for r in DEV) >= 3, \
        "중도해지 질문에 약정금리 이자계산 문장을 근거로 잘못 쓰는지"
    assert any("재예치" in r["tags"] for r in DEV), "재예치 계좌"
    assert any(r["gold"]["behavior"] == "not_early" for r in DEV), "만기 이후 해지"
    assert sum("hana-deposit-sheet__partial_withdrawal" in (r["gold"].get("chunks") or []) for r in DEV) >= 2, "일부해지"
    assert sum(r["category"] == "aftertax" for r in DEV) >= 2, "세후 이자"
    assert any("주말만기" in r["tags"] for r in DEV) and any("최소가입금액" in r["tags"] for r in DEV)


def test_every_category_is_present_and_the_core_set_is_30_to_50_and_covers_every_category():
    for c in bq.CATEGORIES:
        assert sum(r["category"] == c for r in DEV) >= 3, c
    core = [r for r in DEV if r["core"]]
    assert 30 <= len(core) <= 50
    assert {r["category"] for r in core} == set(bq.CATEGORIES)


# ---------- 정답 검산 ----------
@pytest.mark.parametrize("row", [r for r in ROWS if (r.get("gold") or {}).get("calc")], ids=lambda r: r["id"])
def test_calculation_gold_matches_an_independent_formula(row):
    c, g = row["gold"]["calc"], row["gold"]
    args = c["args"]
    full = {k: args.get(k) for k in ("principal", "start_date", "maturity_date", "termination_date")}
    if g["behavior"] == "ask_missing":                                   # 값이 모자란 문항: 부족한 칸이 정확히 gold.missing이다
        required = [k for k in LABELS if k not in args]
        assert g["missing"] == [LABELS[k] for k in required] and required, row["id"]
        return
    ref = reference({**full, "base_rate": args.get("base_rate")})
    assert ref["status"] == c["status"], row["id"]
    if c["status"] == "ok":
        assert (ref["interest_krw"], ref["rate_pct"]) == (c["interest_krw"], c["rate_pct"]), row["id"]
    if c["status"] == "need_base_rate":
        assert g["missing"] == ["기본금리"] and ref["months"] >= 6


def test_the_seven_documented_cases_have_the_documented_amounts():
    """노션 '테스트 케이스' 표(케이스 A~G)의 금액: 5일차 문항 C1~C7이 그 케이스다."""
    want = {"C1": 547, "C2": 2465, "C3": 8219, "C4": 63013, "C5": 94520, "C6": 273304, "C7": 10958}
    got = {s.split(":")[1]: r["gold"]["calc"]["interest_krw"] for r in DEV for s in r["source"] if s.startswith("tool_check:C") and r["gold"]["behavior"] == "calc_ok"}
    assert got == want


def test_rule_questions_forbid_the_calculator_so_a_wrong_tool_choice_is_counted():
    for r in DEV:
        if r["category"] == "rule":
            assert r["gold"]["tools"] == ["search_terms"] and r["gold"]["tools_allowed"] == [], r["id"]


def test_gold_chunk_ids_are_ids_that_really_exist_in_the_index():
    """정답 청크 id는 실제 검색 실행 기록(eval/runs/*.json)에 나온 id여야 한다: 추측한 id는 검색 평가를 조용히 틀리게 한다."""
    known = set()
    for f in (ROOT / "eval" / "runs").glob("*.json"):
        known |= set(re.findall(r"hana-deposit-[a-z-]+__[a-z0-9_-]+", f.read_text(encoding="utf-8")))
    if not known:
        pytest.skip("검색 실행 기록이 없습니다")
    used = {c for r in DEV for c in (r["gold"].get("chunks") or []) + (r["gold"].get("traps") or [])}
    assert used and used <= known, sorted(used - known)


def test_retrieval_gold_equals_the_original_check_search_gold():
    """3일차 검색 문항의 정답 · 함정 청크가 옮겨 오며 바뀌지 않았다."""
    sys.path.insert(0, str(ROOT / "rag"))
    try:
        import check_search as cs
    except Exception as e:                                               # 검색 쪽 의존성이 없는 환경
        pytest.skip(f"check_search를 불러오지 못했습니다: {e}")
    by_src = {s: r for r in DEV for s in r["source"]}
    for n, (group, question, answers, traps) in enumerate(cs.QUESTIONS, 1):
        r = by_src[f"retrieval:{n}"]
        assert r["question"] == question
        if group != "없음":
            assert set(r["gold"]["chunks"]) == set(answers) and set(r["gold"].get("traps") or []) == set(traps), n


def test_the_tool_check_expectations_survived_the_merge():
    from eval import tool_cases as tc
    by_src = {s: r for r in DEV for s in r["source"]}
    for c in tc.CASES:
        r = by_src[f"tool_check:{c['id']}"]
        assert r["question"] == c["question"]
        if c["group"] == "계산":
            assert r["gold"]["calc"]["args"] == c["expect"]["args"]
        if c["group"] == "부족":
            assert r["gold"]["calc"]["args"] == (c["expect"].get("allowed") or {})


def test_every_labeled_held_out_row_passes_the_freeze_check_and_is_not_in_the_core_set():
    """검증용 줄은 정답이 있으면 고정할 수 있는 상태여야 하고, 대표 문항(core)이나 개발용에 섞이지 않는다."""
    from eval import heldout_tools as ht
    held = [r for r in ROWS if r["split"] == "held_out"]
    labeled = [r for r in held if r.get("gold")]
    assert ht.problems_labeling(labeled) == []
    assert not any(r["core"] for r in held)
    dev_questions = {" ".join(r["question"].split()) for r in DEV}
    assert not any(" ".join(r["question"].split()) in dev_questions for r in held)       # 개발용에서 이미 본 질문을 검증용으로 받지 않는다


def test_the_frozen_held_out_questions_have_not_been_changed():
    """고정 뒤에는 검증용 문항과 정답을 바꿀 수 없다: 고정 기록(eval/held_out_manifest.json)이 있으면 지문이 그대로여야 한다 (실수로 questions.jsonl을 고치면 여기서 잡힌다)."""
    from eval import heldout_tools as ht
    manifest = ROOT / "eval" / "held_out_manifest.json"
    if not manifest.exists():
        pytest.skip("아직 고정되지 않았습니다")
    assert ht.verify() == []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["count"] == len(data["fingerprints"]) == len([r for r in ROWS if r["split"] == "held_out"])
