"""평가 문항 만들기 (10일차): 흩어져 있던 평가 자료를 한 형식(eval/questions.jsonl)으로 모으고, 필수 시나리오에서 비어 있던 칸을 새 개발용 문항으로 채운다.

실행: 레포 루트에서  python eval/build_questions.py            (eval/questions.jsonl을 다시 만든다)
      python eval/build_questions.py --check    (파일이 빌더의 결과와 같은지만 확인, 바꾸지 않는다)

원칙
  - 정답은 손으로 옮기지 않고 기존 자료에서 파생한다: 도구 · 인자는 eval/tool_cases.py, 값은 eval/heldout/questions.json, 정답 청크는 rag/check_search.py.
    계산 정답(상태 · 금액 · 부족한 칸)은 계산 도구로 구한다 (시험이 도구와 별개의 공식으로 다시 검산한다: tests/test_eval_set.py).
  - 모든 문항이 split=dev다. 6일차 검증용 20문항은 실패 유형을 보고 규칙을 고쳤으므로 tags에 contaminated를 달아 개발용으로 격하했다.
    split=held_out은 시스템을 모르는 사람이 쓴 새 문항만 들어간다 (eval/intake_heldout.py → 정답 작성 → eval/freeze_heldout.py).
  - core=true는 30~50문항으로 줄인 대표 문항(분류별로 고르게)이다. 기존 챗봇과 비교할 때는 core만 쓴다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "rag"))

from app.tools.interest import calc_interest  # noqa: E402

OUT = ROOT / "eval" / "questions.jsonl"
TODAY = "2026-10-28"
CATEGORIES = ("rule", "aftertax", "calc", "calc_base_rate", "missing", "handoff", "scope")
BEHAVIORS = ("search", "calc_ok", "ask_base_rate", "ask_missing", "not_early", "invalid_input", "handoff_saved", "handoff_confirm", "no_tool", "scope")
CALC_BEHAVIOR = {"ok": "calc_ok", "need_base_rate": "ask_base_rate", "not_early_termination": "not_early", "invalid_input": "invalid_input", "missing_info": "ask_missing"}
CALC_CATEGORY = {"ok": "calc", "need_base_rate": "calc_base_rate", "not_early_termination": "calc", "invalid_input": "calc", "missing_info": "missing"}

# 정답 청크 (rag/check_search.py의 id). 5일차 문항 중 검색 문항과 겹치지 않는 3개는 비슷한 검색 문항에서 추정했다 (note에 밝힘).
INFERRED_CHUNKS = {
    "T2": (["hana-deposit-sheet__after_maturity"], "검색 문항 #11(만기 후 이자)에서 추정"),
    "T4": (["hana-deposit-sheet__renewal", "hana-deposit-terms__art10"], "검색 문항 #13(자동 재예치)에서 추정"),
    "T6": (["hana-deposit-sheet__tax"], "검색 문항 #19(비과세종합저축)에서 추정"),
}
# core(대표 문항) 선택: 소스 id. 규칙 질문은 12개, 나머지 분류는 거의 모두.
CORE_SOURCES = {
    "retrieval:1", "retrieval:3", "retrieval:5", "retrieval:11", "retrieval:12", "retrieval:13", "retrieval:14", "retrieval:16", "retrieval:18", "retrieval:24",
    "tool_check:T2", "heldout:H15",
    "tool_check:C2", "tool_check:C4", "tool_check:C5", "tool_check:C6", "tool_check:C8", "tool_check:E1",
    "tool_check:M1", "tool_check:M2", "tool_check:M4", "tool_check:M5",
    "heldout:H1", "heldout:H2", "heldout:H3", "heldout:H5", "heldout:H6", "heldout:H8", "heldout:H13", "heldout:H16", "heldout:H19", "heldout:H20",
    "tool_check:N1", "tool_check:N3", "retrieval:32", "retrieval:31",
    "new_dev:coupon1", "new_dev:reissue_calc", "new_dev:reissue_rule", "new_dev:handoff_keyword1", "new_dev:handoff_keyword2", "new_dev:handoff_confirm",
    "new_dev:handoff_fee", "new_dev:tax_rule", "new_dev:after_maturity_rate", "new_dev:weekend",
}


def _calc_item(args: dict, behavior_hint: str | None = None) -> tuple[str, str, dict]:
    """계산 도구로 정답을 구한다 → (분류, 행동, gold). ask_missing은 아는 값만 넣어 부르거나 안 불러도 된다."""
    r = calc_interest(**args)
    st = r["status"]
    calc = {"args": args, "status": st}
    gold = {"behavior": CALC_BEHAVIOR[st], "tools": ["calc_interest"], "tools_allowed": [], "calc": calc}
    if st == "ok":
        calc["interest_krw"], calc["rate_pct"] = r["interest_krw"], r["rate_pct"]
    if st in ("missing_info", "need_base_rate"):
        gold["missing"] = r["missing"]
    if st == "missing_info":
        gold["tools"], gold["tools_allowed"] = [], ["calc_interest"]
    return CALC_CATEGORY[st], CALC_BEHAVIOR[st], gold


def _rule_gold(chunks: list[str], traps: list[str] | None = None) -> dict:
    g = {"behavior": "search", "tools": ["search_terms"], "tools_allowed": [], "chunks": sorted(chunks)}
    if traps:
        g["traps"] = sorted(traps)
    return g


def _entry(source: list[str], category: str, question: str, gold: dict, tags: list[str], note: str = "") -> dict:
    return {"source": source, "category": category, "question": question, "today": TODAY, "gold": gold, "tags": sorted(set(tags)), "note": note}


def from_retrieval() -> list[dict]:
    import check_search as cs
    out = []
    for n, (group, question, answers, traps) in enumerate(cs.QUESTIONS, 1):
        tags = ["tuned" if not group.startswith("검증") and group != "없음" else "validation_retrieval" if group.startswith("검증") else "tuned", group]
        if group == "없음":
            if "세후" in question:
                gold = {"behavior": "scope", "tools": [], "tools_allowed": ["search_terms", "calc_interest"], "must_say_any": ["세전", "원천징수", "세금"], "weak_check": True}
                out.append(_entry([f"retrieval:{n}"], "aftertax", question, gold, tags + ["세후"], "세후 이자는 계산하지 않고 세전 이자와 원천징수 근거(기본약관 제9조 ⑤)를 안내해야 한다"))
            else:
                gold = {"behavior": "scope", "tools": [], "tools_allowed": ["search_terms"], "must_not": ["접수번호"], "weak_check": True}
                out.append(_entry([f"retrieval:{n}"], "scope", question, gold, tags, "범위 밖: 계산 · 상담 요청 저장 없이 약관에 없다고 하거나 도울 수 없다고 말해야 한다"))
            continue
        out.append(_entry([f"retrieval:{n}"], "rule", question, _rule_gold(list(answers), list(traps)), tags + (["중도해지"] if "중도해지" in group else [])))
    return out


def from_tool_check() -> list[dict]:
    from eval import tool_cases as tc
    out = []
    for c in tc.CASES:
        e, cid, q = c["expect"], c["id"], c["question"]
        src = [f"tool_check:{cid}"]
        if c["group"] == "약관":
            chunks, why = INFERRED_CHUNKS.get(cid, (None, ""))
            gold = _rule_gold(chunks or [])
            if not chunks:
                gold.pop("chunks")
            out.append(_entry(src, "rule", q, gold, ["tool_check"], why))
        elif c["group"] in ("계산", "엣지"):
            cat, _, gold = _calc_item(e["args"])
            out.append(_entry(src, cat, q, gold, ["tool_check"] + (["최소가입금액"] if cid == "E1" else [])))
        elif c["group"] == "부족":
            cat, _, gold = _calc_item(e.get("allowed") or {})
            out.append(_entry(src, cat, q, gold, ["tool_check"], "아는 값만 넣어 부르거나 안 부르고 되물어도 통과. 말하지 않은 값을 지어내면 안 됨"))
        else:                                                         # 불필요: 인사 · 범위 밖
            if "날씨" in q:
                gold = {"behavior": "scope", "tools": [], "tools_allowed": ["search_terms"], "must_not": ["접수번호", "기온", "맑", "흐림"], "weak_check": True}
                out.append(_entry(src, "scope", q, gold, ["tool_check"]))
            else:
                out.append(_entry(src, "scope", q, {"behavior": "no_tool", "tools": [], "tools_allowed": []}, ["tool_check", "인사"]))
    return out


def from_heldout() -> list[dict]:
    q = json.loads((ROOT / "eval" / "heldout" / "questions.json").read_text(encoding="utf-8"))["questions"]
    out = []
    for i in q:
        hid, expect, gold_h = i["id"], i["expect"], i["gold"]
        src, tags = [f"heldout:{hid}"], ["contaminated", "heldout_h"]
        if expect in ("search", "search_ask"):
            out.append(_entry(src, "rule", i["question"], _rule_gold([]) | {"chunks": None}, tags, i.get("note", "")))
            continue
        args = {k: v["value"] for k, v in gold_h.items()}
        cat, _, gold = _calc_item(args)
        if any(v["kind"] == "derived" for v in gold_h.values()):
            tags.append("derived_value")
        if hid == "H6":
            tags.append("unsupported_date")                          # '7월 말'은 코드가 읽지 못한다 (알려진 한계)
            gold["calc"]["args"] = {**args, "termination_date": "2026-07-31"}
            cat, _, gold = _calc_item(gold["calc"]["args"])
            gold["unsupported"] = ["termination_date"]
        if hid == "H20":
            cat = "aftertax"
            gold["must_not"] = ["세금을 뺀[^.?!]*알려\\s?드릴 수 있", "세후[^.?!]*알려\\s?드"]
        out.append(_entry(src, cat, i["question"], gold, tags, i.get("note", "")))
    for e in out:                                                     # chunks=None은 정답 청크 미정 (검색 질문에서 청크 정답이 없는 것)
        if e["gold"].get("chunks", 1) is None:
            e["gold"].pop("chunks")
    return out


def new_dev() -> list[dict]:
    """필수 시나리오 중 기존 자료에 없던 칸을 채우는 새 개발용 문항 (시스템을 아는 사람이 썼으므로 dev)."""
    out = []
    # 쿠폰 · 적용금리를 기본금리로 말하는 경우: 기본금리와 적용금리가 다를 수 있다고 알리고 추정으로 표시해야 한다 (설계 의도, 아직 미구현)
    for slug, q, args in (
        ("coupon1", "앱에서 보니까 제 예금 금리가 연 3.5%였어요. 1000만원을 2026-01-01에 넣었고 만기는 2027-01-01인데 2026-12-07에 해지하면 이자 얼마예요?",
         {"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": "2026-12-07", "base_rate": 3.5}),
        ("coupon2", "쿠폰 우대금리까지 합쳐서 연 4.0%로 가입했어요. 2000만원, 신규 2026-01-01, 만기 2027-01-01이고 오늘 해지하면 이자 얼마예요?",
         {"principal": 20_000_000, "start_date": "2026-01-01", "maturity_date": "2027-01-01", "termination_date": TODAY, "base_rate": 4.0}),
    ):
        cat, _, gold = _calc_item(args)
        gold["must_say_any"] = ["추정", "적용금리", "다를 수"]
        out.append(_entry([f"new_dev:{slug}"], cat, q, gold, ["적용금리", "known_gap"], "앱에서 본 적용금리는 기본금리와 다를 수 있다: 입력으로 쓰면 추정값이라고 알려야 한다(설계 의도, 미구현)"))
    # 재예치 계좌: 신규일은 최종 재예치일로 본다
    cat, _, gold = _calc_item({"principal": 10_000_000, "start_date": "2026-01-10", "maturity_date": "2027-01-10", "termination_date": TODAY})
    gold["must_say_any"] = ["재예치"]
    out.append(_entry(["new_dev:reissue_calc"], cat, "작년 3월에 가입했다가 올해 1월 10일에 자동으로 재예치된 예금이에요. 1000만원이고 만기는 2027-01-10인데 오늘 해지하면 이자 얼마예요?", gold,
                      ["재예치", "known_gap"], "재예치된 계좌는 신규일을 최종 재예치일(2026-01-10)로 본다. 그 점을 알리고 기본금리를 되물어야 한다(설계 의도, 미구현)"))
    out.append(_entry(["new_dev:reissue_rule"], "rule", "자동으로 재예치된 예금을 중도해지하면 기간은 언제부터 세나요?",
                      _rule_gold(["hana-deposit-sheet__early_termination", "hana-deposit-sheet__renewal"]), ["재예치"], "정답 청크는 설명서의 중도해지 · 재예치 항목에서 추정"))
    # 상담원 연결
    for slug, q, behavior, tags, note in (
        ("handoff_keyword1", "상담원 연결해줘", "handoff_saved", [], "키워드로 바로 저장"),
        ("handoff_keyword2", "직원이랑 통화하고 싶어요", "handoff_saved", [], "키워드로 바로 저장"),
        ("handoff_confirm", "사람이랑 얘기하고 싶어요", "handoff_confirm", ["model_dependent"], "키워드에 안 걸림: 모델이 request_handoff를 불러야 저장 확인 질문이 나온다 (모델 변동)"),
    ):
        gold = {"behavior": behavior, "tools": ["request_handoff"], "tools_allowed": [], "must_say_any": ["접수번호"] if behavior == "handoff_saved" else ["저장할까요"]}
        out.append(_entry([f"new_dev:{slug}"], "handoff", q, gold, tags, note))
    out.append(_entry(["new_dev:handoff_fee"], "scope", "상담원 연결하면 수수료가 있나요?",
                      {"behavior": "scope", "tools": [], "tools_allowed": ["search_terms"], "must_not": ["접수번호"], "weak_check": True}, ["known_gap", "keyword_false_positive"],
                      "상담 요청이 아니라 질문이다. 키워드 때문에 저장 요청으로 오인하면 안 된다 (알려진 한계)"))
    # 세금 · 만기 후 · 주말 만기 · 계약기간 초과
    out.append(_entry(["new_dev:tax_rule"], "aftertax", "예금 이자에 세금이 붙나요?",
                      _rule_gold(["hana-deposit-sheet__tax", "hana-deposit-basic-terms__art9"]) | {"must_say_any": ["원천징수", "세금", "과세"]}, ["세후"],
                      "정답 청크는 설명서의 세금 항목과 기본약관 제9조에서 추정. 세후 금액은 계산하지 않는다"))
    out.append(_entry(["new_dev:after_maturity_rate"], "rule", "만기가 지난 뒤 찾으면 이자율이 정확히 몇 퍼센트인지 숫자로 알려 주세요",
                      _rule_gold(["hana-deposit-sheet__after_maturity"]) | {"must_say_any": ["확인할 수 없", "확인되지", "고시"]}, ["만기이후"],
                      "문서에는 만기 후 금리의 산식만 있고 기준이 되는 고시금리 숫자가 없다: 숫자를 지어내면 안 된다"))
    cat, _, gold = _calc_item({"principal": 10_000_000, "start_date": "2025-12-05", "maturity_date": "2026-12-05", "termination_date": "2026-12-04", "base_rate": 3.5})
    gold["must_say_any"] = ["만기 해지", "은행에 확인", "휴일"]
    out.append(_entry(["new_dev:weekend"], cat, "1000만원을 2025-12-05에 가입했고 만기는 2026-12-05예요. 2026-12-04에 해지하면 이자 얼마예요? 기본금리는 3.5%예요.", gold, ["주말만기"],
                      "만기일이 토요일이고 해지일이 직전 영업일: 계산 결과와 함께 만기 해지로 처리될 수 있다는 경고(근거 카드)가 나와야 한다"))
    cat, _, gold = _calc_item({"principal": 10_000_000, "start_date": "2026-01-01", "maturity_date": "2032-01-01", "termination_date": TODAY})
    out.append(_entry(["new_dev:term_limit"], cat, "1000만원을 2026-01-01에 넣고 2032-01-01 만기로 했는데 오늘 해지하면 이자 얼마예요?", gold, ["계약기간초과"], "계약기간은 5년 이내"))
    return out


def build() -> list[dict]:
    """모든 출처를 합치고(같은 질문은 하나로), 분류 순서대로 id를 매기고, core를 표시한다."""
    merged: dict[str, dict] = {}
    for e in from_retrieval() + from_tool_check() + from_heldout() + new_dev():
        key = " ".join(e["question"].split())
        if key in merged:                                             # 같은 질문: 출처 · 태그를 합치고 정답 청크는 검색 문항 쪽을 쓴다
            m = merged[key]
            m["source"] = m["source"] + [s for s in e["source"] if s not in m["source"]]
            m["tags"] = sorted(set(m["tags"]) | set(e["tags"]))
            for k in ("chunks", "traps"):
                if k in e["gold"] and k not in m["gold"]:
                    m["gold"][k] = e["gold"][k]
            continue
        merged[key] = e
    order = {c: i for i, c in enumerate(CATEGORIES)}
    rows = sorted(merged.values(), key=lambda e: (order[e["category"]], e["source"][0].split(":")[0] != "retrieval", _source_order(e["source"][0])))
    out = []
    for n, e in enumerate(rows, 1):
        out.append({"id": f"Q{n:03d}", "split": "dev", "core": any(s in CORE_SOURCES for s in e["source"]), **e})
    return out


def _source_order(src: str) -> tuple:
    kind, _, ident = src.partition(":")
    digits = "".join(ch for ch in ident if ch.isdigit())
    return (kind, ident[:1], int(digits) if digits else 0)


def dumps(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n" for r in rows)


def _shown(path: Path) -> str:
    """출력용 경로: 레포 안이면 레포 기준, 밖이면 그대로."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def existing_held_out(path=None) -> list[dict]:
    """파일에 이미 있는 검증용(held_out) 줄. 빌더는 개발용만 만들고 검증용은 손대지 않는다 (고정한 문항이 지워지면 안 된다)."""
    path = Path(path or OUT)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("split") == "held_out"]


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    dev_text = dumps(build())
    if "--check" in args:                                             # 개발용 부분만 비교한다
        current = "".join(line + "\n" for line in OUT.read_text(encoding="utf-8").splitlines() if line.strip() and json.loads(line).get("split") != "held_out") if OUT.exists() else ""
        same = current == dev_text
        print("eval/questions.jsonl의 개발용 부분은 빌더의 결과와 같습니다." if same else "eval/questions.jsonl의 개발용 부분이 빌더의 결과와 다릅니다 (python eval/build_questions.py로 다시 만드세요).")
        return 0 if same else 1
    kept = existing_held_out()
    text = dev_text + dumps(kept)
    OUT.write_text(text, encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    from collections import Counter
    print(f"{len(rows)}문항 (개발용 {len(rows) - len(kept)} · 검증용 {len(kept)}, core {sum(r['core'] for r in rows)}) → {_shown(OUT)}")
    print("분류:", dict(Counter(r["category"] for r in rows)))
    print("행동:", dict(Counter(r["gold"]["behavior"] for r in rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
