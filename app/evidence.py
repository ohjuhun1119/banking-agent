"""근거 표시: 답변 옆에 코드가 붙이는 근거 카드 (모델이 쓰지 않는다).

왜 코드가 붙이는가: 7B 모델은 근거에 없는 결론을 약관 근거처럼 말하고(`약관에서 확인된 정보에 따르면 …`), 규칙표와 다르게 요약한다
(검증용 실제 모델 결과 H17 · H12). 모델에게 근거를 쓰게 하면 근거도 지어낼 수 있으므로, 도구가 돌려준 것만 그대로 보여준다.

카드 두 종류
  계산   calc_interest 결과가 ok일 때: 한 줄 요약, 계산식, 사용한 가정, 경고 (도구가 돌려준 문장 그대로)
  약관   검색 결과의 출처 정보: 문서 · 조항 · 쪽 · 심의번호 · 적용일 · 기준일 (본문은 넣지 않는다)
         - 약관 질문: 이번 질문에서 검색된 결과 위쪽 3개
         - 계산 답변(ok · need_base_rate): 중도해지 구간표를 담은 `중도해지` 청크의 출처 (검색 결과에 없으면 코드가 한 번 찾아본다)
         - 검색 결과가 없으면 '관련 약관을 찾지 못했다'는 카드

한계: 약관 카드는 '검색된' 조항이지 모델이 '실제로 근거로 쓴' 조항이 아니다 (위쪽 3개를 그대로 보여준다).
"""
from __future__ import annotations

import re

LOOKUP_QUERY = "중도해지 금리 구간 산출 근거"
RULE_CHUNK_TYPE = "중도해지"
MAX_TERMS_CARDS = 3
SOURCE_KEYS = ("doc_title", "article", "section", "page", "review_no", "effective_date", "as_of")

# 계산을 알려 달라는 질문(이자가 얼마인지): 계산 도구로 값을 되묻는다. 방법 · 규칙을 묻는 질문은 약관 검색으로 보낸다.
_AMOUNT_SEEK = re.compile(r"(이자|이율|금리)[^.?!]{0,12}(얼마|차이|손해|이득|나와|붙)|얼마[^.?!]{0,6}(받|나와|붙|들어)|얼마나[^.?!]{0,4}(받|나와|붙|손해|이득)")
_METHOD = re.compile(r"어떻게|방법|기준|규칙|계산\s?(되|돼|하는|방식|법)|산출|적용\s?(되|돼)|왜")


def calc_intent(question) -> bool:
    """'이자가 얼마예요?' · '이자 차이가 많이 나나요?'처럼 금액을 알려 달라는 질문인가. '이율이 어떻게 돼요?' · '이자 계산되나요?'는 아니다."""
    if not isinstance(question, str):
        return False
    return bool(_AMOUNT_SEEK.search(question)) and not _METHOD.search(question)


def source_card(result: dict, role: str) -> dict:
    """검색 결과 하나의 출처 카드 (본문 text는 넣지 않는다)."""
    card = {"kind": "약관", "role": role, "chunk_id": result.get("chunk_id"), "chunk_type": result.get("chunk_type")}
    card.update({k: v for k, v in (result.get("source") or {}).items() if k in SOURCE_KEYS and v not in (None, "")})
    return card


def calc_card(result: dict) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    card = {"kind": "계산", "summary": result.get("summary")}
    for key in ("formula", "assumptions", "warnings"):
        if result.get(key):
            card[key] = list(result[key])
    return card


def _ok_results(tool_results: list) -> list[dict]:
    out, seen = [], set()
    for name, res in tool_results:
        if name == "search_terms" and isinstance(res, dict) and res.get("status") == "ok":
            for r in res.get("results") or []:
                if r.get("chunk_id") not in seen:
                    seen.add(r.get("chunk_id"))
                    out.append(r)
    return out


def build_evidence(tool_results: list, rule_lookup=None) -> list[dict]:
    """이번 질문의 도구 결과 [(이름, 결과 dict)]로 근거 카드 목록을 만든다.

    rule_lookup: 인자 없이 호출하면 검색 결과 dict를 돌려주는 함수 (계산 답변에 붙일 중도해지 규칙 청크를 찾을 때만 쓴다).
    """
    cards: list[dict] = []
    calc = [res for name, res in tool_results if name == "calc_interest" and isinstance(res, dict)]
    ok_calc = next((c for c in calc if c.get("status") == "ok"), None)
    need_rate = any(c.get("status") == "need_base_rate" for c in calc)
    found = _ok_results(tool_results)
    if ok_calc:
        cards.append(calc_card(ok_calc))
    if ok_calc or need_rate:                                              # 계산 답변: 중도해지 구간표의 출처
        rule = next((r for r in found if r.get("chunk_type") == RULE_CHUNK_TYPE), None)
        if rule is None and rule_lookup is not None:
            try:
                looked = rule_lookup()
            except Exception:                                             # 근거를 못 찾아도 답변은 막지 않는다
                looked = {}
            rule = next((r for r in (looked or {}).get("results") or [] if r.get("chunk_type") == RULE_CHUNK_TYPE), None) if (looked or {}).get("status") == "ok" else None
        if rule is not None:
            cards.append(source_card(rule, "중도해지금리 규칙(계산에 쓴 구간표)" if ok_calc else "기본금리가 필요한 구간(경과 6개월 이상)"))
    if found and not (ok_calc or need_rate):                              # 약관 질문: 검색된 결과 위쪽
        cards += [source_card(r, "검색된 약관") for r in found[:MAX_TERMS_CARDS]]
    elif not found and not (ok_calc or need_rate) and any(n == "search_terms" and isinstance(r, dict) and r.get("status") == "not_found" for n, r in tool_results):
        cards.append({"kind": "약관", "found": False, "role": "관련 약관을 찾지 못했습니다"})      # 검색이 not_found일 때만 (읽을 수 없는 결과로는 단정하지 않는다)
    return cards


def _where(card: dict) -> str:
    parts = [card.get("doc_title"), card.get("article") or card.get("section")]
    if card.get("page") not in (None, ""):
        parts.append(f"쪽 {card['page']}")
    if card.get("review_no"):
        parts.append(f"심의 {card['review_no']}")
    if card.get("effective_date"):
        parts.append(f"적용일 {card['effective_date']}")
    if card.get("as_of"):
        parts.append(f"기준일 {card['as_of']}")
    return " · ".join(str(p) for p in parts if p)


def format_evidence(cards: list[dict]) -> str:
    """근거 카드를 글로 만든다 (화면 · 명령줄 · API가 같은 문장을 쓴다). 카드가 없으면 빈 문자열."""
    if not cards:
        return ""
    lines = ["근거"]
    for c in cards:
        if c.get("kind") == "계산":
            lines.append(f"- [계산] {c.get('summary') or ''}".rstrip())
            lines += [f"  · {f}" for f in c.get("formula", [])]
            lines += [f"  · 가정: {a}" for a in c.get("assumptions", [])]
            lines += [f"  · 주의: {w}" for w in c.get("warnings", [])]
        elif c.get("found") is False:
            lines.append(f"- [약관] {c.get('role')}")
        else:
            lines.append(f"- [약관] {_where(c)} ({c.get('role')})")
    return "\n".join(lines)
