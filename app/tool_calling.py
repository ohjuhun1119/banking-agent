"""5일차: 로컬 LLM(qwen2.5:7b)에 도구 2개(search_terms, calc_interest)를 연결한다 (Tool Calling).

여기서는 '모델 1회 호출 -> 어떤 도구를 어떤 값으로 부르는지 확인 -> (선택) 도구 실행'까지만 한다.
도구 결과를 모델에 다시 넘겨 추가 호출 / 되묻기 / 최종 답변을 고르는 반복은 6일차(LangGraph)에서 만든다.

설계 결정
  1. 도구 정의는 pydantic 대신 OpenAI 함수 형식 dict로 직접 쓴다. Optional 타입이 anyOf로 바뀌어 모델 · Ollama가
     혼동하는 일을 피하고, 모델이 실제로 보는 스키마를 그대로 확인하기 위해서다 (tool_specs()).
  2. 검색 도구는 query만 받고 상품명은 코드가 고정한다. search_terms의 product 필터는 [상품명, 공통]만 허용해서,
     모델이 '정기예금'처럼 조금만 다르게 쓰면 상품 전용 문서(설명서 · 특약)가 오류 없이 조용히 빠진다.
  3. calc_interest의 인자는 모두 선택이다. 모르는 값은 비우고 부르면 도구가 missing_info로 알려준다.
     모델이 값을 지어내지 않는지가 핵심 확인 항목이다.
  4. 날짜는 YYYY-MM-DD 문자열 그대로 넘기고 일수로 바꾸지 않는다. 오늘 날짜는 시스템 프롬프트로 알려준다.
  5. 시스템 프롬프트는 고정하고 도구 설명 문구만 3종(short · detailed · examples)으로 바꿔 비교한다.

실행: 레포 루트에서  python -m app.tool_calling "1000만원 2026-01-01 신규 2027-01-01 만기 2026-05-31 해지하면 이자는?"
      python -m app.tool_calling --variant short --run "중도해지하면 이율이 어떻게 돼요?"     (--run: 도구까지 실행)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))        # rag/ 스크립트가 'from search import ...' 방식이라 경로를 추가한다

from app.tools.interest import calc_interest  # noqa: E402

PRODUCT = "하나의 정기예금"                     # 검색 필터에 쓰는 상품명 (chunks.jsonl의 product 값과 같아야 한다)
TOOL_MODEL = os.environ.get("TOOL_MODEL", os.environ.get("CHAT_MODEL", "qwen2.5:7b"))
NUM_CTX = int(os.environ.get("CHAT_NUM_CTX", "8192"))
VARIANTS = ("short", "detailed", "examples")
DEFAULT_VARIANT = os.environ.get("TOOL_VARIANT", "detailed")
CALC_ARGS = ("principal", "start_date", "maturity_date", "termination_date", "base_rate")
TOOL_NAMES = ("search_terms", "calc_interest")
HANDOFF_NAME = "request_handoff"                 # 에이전트 그래프에서만 모델에 붙이는 세 번째 도구 (5일차 측정 대상이 아님)
HANDOFF_SPEC = {"type": "function", "function": {
    "name": HANDOFF_NAME,
    "description": ("사용자가 사람 상담원과 얘기하고 싶다거나 연결을 원한다는 뜻을 보일 때만 부른다 (예: '사람이랑 얘기하고 싶어요'). "
                    "이 도구는 저장하지 않는다: 저장해도 되는지 사용자에게 물어볼 문장을 돌려준다. 약관 질문이나 이자 계산 질문에는 부르지 않는다."),
    "parameters": {"type": "object", "properties": {}, "required": []}}}

SYSTEM_PROMPT = """너는 하나은행 『하나의 정기예금』 상담 도우미다. 오늘 날짜는 {today}이다.
필요하면 도구를 호출하고, 도구가 필요 없는 질문에는 도구 없이 짧게 답한다.
도구에 넘기는 값은 사용자가 말한 것만 쓴다. 사용자가 말하지 않은 금액 · 날짜 · 금리는 추측해서 넣지 않는다."""

# ---------- 도구 설명 문구 3종 ----------
# short: 한 줄 / detailed: 사용 조건 · 형식 · 지어내지 말 것 / examples: detailed + 예시
# examples의 예시는 확인용 질문(eval/tool_cases.py)과 겹치지 않게 쓴다 (tests/test_tool_calling.py가 확인).
SEARCH_DESC = {
    "short": "『하나의 정기예금』 약관과 상품설명서를 검색한다.",
    "detailed": ("『하나의 정기예금』 상품설명서와 약관(특약, 거치식예금 약관, 예금거래 기본약관)에서 관련 조항을 검색한다. "
                 "금리 규칙, 중도해지 이율, 만기 후 이자, 일부해지, 재예치, 세금, 예금자보호 같은 상품 규칙을 물으면 사용한다. "
                 "결과에는 조항 본문과 근거 정보(문서명, 조항, 기준일)가 들어 있다. 이자 금액 계산에는 쓰지 않는다."),
}
SEARCH_DESC["examples"] = SEARCH_DESC["detailed"] + (
    " 예: '적용금리와 기본금리는 뭐가 달라요?' -> query='기본금리 적용금리 차이', "
    "'압류된 예금도 찾을 수 있나요?' -> query='압류 원금 이자 지급 제한'")

CALC_DESC = {
    "short": "정기예금 중도해지 이자를 계산한다.",
    "detailed": ("『하나의 정기예금』을 만기 전에 해지할 때의 세전 이자를 계산한다. 사용자가 이자 금액을 물으면 사용한다. "
                 "사용자가 말한 값만 넣고 모르는 값은 비워 둔다 (도구가 부족한 값을 알려준다). "
                 "날짜는 YYYY-MM-DD 형식 그대로 넣고 일수나 개월 수로 바꾸지 않는다. '몇 개월 전', '며칠 지났어요'처럼 "
                 "날짜가 정확하지 않으면 날짜를 계산해 넣지 말고 비워 둔다. 원금은 원 단위 숫자로 넣는다 (1000만원 -> 10000000)."),
}
CALC_DESC["examples"] = CALC_DESC["detailed"] + (
    " 예: '3000만원을 2025-04-10에 넣었고 만기는 2026-04-10, 2025-08-01에 해지' -> principal=30000000, "
    "start_date='2025-04-10', maturity_date='2026-04-10', termination_date='2025-08-01'. "
    "'예금 깨면 이자 얼마예요?' -> 아는 값이 없으므로 인자 없이 호출")

QUERY_DESC = {
    "short": "검색어",
    "detailed": "검색할 내용. 사용자의 질문을 약관 용어로 짧게 바꾼 문장 (예: '중도해지 금리'). 상품명은 넣지 않아도 된다.",
}
QUERY_DESC["examples"] = QUERY_DESC["detailed"]

ARG_DESC = {
    "principal": {"short": "원금(원)", "detailed": "원금. 원 단위 정수 (예: 10000000). 사용자가 말하지 않았으면 비운다."},
    "start_date": {"short": "신규일", "detailed": "신규일(가입일) YYYY-MM-DD. 재예치 계좌는 최종 재예치일. 모르면 비운다."},
    "maturity_date": {"short": "만기일", "detailed": "만기일 YYYY-MM-DD. 모르면 비운다."},
    "termination_date": {"short": "해지일", "detailed": "해지일 YYYY-MM-DD. '오늘' 해지면 오늘 날짜. 모르면 비운다."},
    "base_rate": {"short": "기본금리(%)", "detailed": ("신규일 당시 고시된 계약기간별 기본금리(%, 예: 3.5). "
                                                    "사용자가 말한 경우에만 넣는다. 경과 6개월 이상일 때만 계산에 쓰인다.")},
}
ARG_TYPES = {"principal": "integer", "start_date": "string", "maturity_date": "string",
             "termination_date": "string", "base_rate": "number"}


def tool_specs(variant: str = DEFAULT_VARIANT) -> list[dict]:
    """모델에 넘기는 도구 정의 (OpenAI 함수 형식). 확인 화면의 '모델이 보는 도구 정의'와 같은 값이다."""
    if variant not in VARIANTS:
        raise ValueError(f"알 수 없는 설명 문구: {variant} ({', '.join(VARIANTS)} 중에서 고르세요)")
    arg_level = "short" if variant == "short" else "detailed"
    return [
        {"type": "function", "function": {
            "name": "search_terms", "description": SEARCH_DESC[variant],
            "parameters": {"type": "object",
                           "properties": {"query": {"type": "string", "description": QUERY_DESC[variant]}},
                           "required": ["query"]}}},
        {"type": "function", "function": {
            "name": "calc_interest", "description": CALC_DESC[variant],
            "parameters": {"type": "object",
                           "properties": {a: {"type": ARG_TYPES[a], "description": ARG_DESC[a][arg_level]} for a in CALC_ARGS},
                           "required": []}}},
    ]


def build_messages(question: str, today: str | None = None) -> list[dict]:
    today = today or date.today().isoformat()
    return [{"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
            {"role": "user", "content": question}]


class ToolCallingError(RuntimeError):
    """모델 호출 실패 (Ollama가 꺼져 있음, 모델 없음 등)."""


def get_model(variant: str = DEFAULT_VARIANT, extra_tools=()):
    """도구가 연결된 ChatOllama. temperature 0으로 같은 질문에 같은 결과가 나오게 한다 (흔들림은 --repeat로 확인). extra_tools: 에이전트가 더 붙이는 도구 정의."""
    from langchain_ollama import ChatOllama
    return ChatOllama(model=TOOL_MODEL, temperature=0, num_ctx=NUM_CTX).bind_tools(tool_specs(variant) + list(extra_tools))


def _to_lc(messages: list[dict]):
    from langchain_core.messages import HumanMessage, SystemMessage
    return [SystemMessage(m["content"]) if m["role"] == "system" else HumanMessage(m["content"]) for m in messages]


def ask(question: str, variant: str = DEFAULT_VARIANT, today: str | None = None, model=None) -> dict:
    """모델을 한 번 호출해서 도구 호출 목록을 돌려준다. 도구는 실행하지 않는다.

    반환: {"question", "variant", "tool_calls": [{"name", "args"}], "invalid_calls": [...], "content", "ms"}
    model을 주면 그 모델을 쓴다 (시험용 가짜 모델). invoke(messages) -> AIMessage 비슷한 객체면 된다.
    """
    model = model or get_model(variant)
    t0 = time.perf_counter()
    try:
        msg = model.invoke(_to_lc(build_messages(question, today)))
    except Exception as e:                                   # 연결 실패 · 모델 없음 등은 원인을 알 수 있게 감싸서 올린다
        raise ToolCallingError(f"모델 호출 실패 ({TOOL_MODEL}): {e}. 'ollama serve'와 'ollama pull {TOOL_MODEL}'를 확인하세요.") from e
    ms = int((time.perf_counter() - t0) * 1000)
    calls = [{"name": c["name"], "args": dict(c.get("args") or {})} for c in (getattr(msg, "tool_calls", None) or [])]
    invalid = [{"name": c.get("name"), "args": c.get("args"), "error": c.get("error")}
               for c in (getattr(msg, "invalid_tool_calls", None) or [])]
    content = msg.content if isinstance(getattr(msg, "content", ""), str) else str(msg.content)
    return {"question": question, "variant": variant, "tool_calls": calls, "invalid_calls": invalid,
            "content": content, "ms": ms}


# ---------- 도구 실행 ----------
def _compact_search(res: dict) -> dict:
    """모델에 돌려줄 검색 결과: 순위, 청크 종류, 근거 정보, 본문만 남긴다 (점수 · 시간은 뺀다)."""
    if res.get("status") != "ok":
        return {k: res[k] for k in ("status", "message", "query") if k in res}
    return {"status": "ok", "query": res.get("query"),
            "results": [{"rank": r["rank"], "chunk_id": r["chunk_id"], "chunk_type": r.get("chunk_type"),
                         "source": r.get("source", {}), "text": r["text"]} for r in res["results"]]}


def run_tool(name: str, args: dict | None, search_fn=None, k: int = 5) -> dict:
    """모델이 고른 도구를 실행한다. 어떤 경우에도 예외 대신 status가 있는 dict를 돌려준다 (에이전트가 읽을 수 있게).

    search_fn: 시험용으로 검색 함수를 바꿔 끼울 때 쓴다 (기본은 rag/search.py의 search_terms).
    """
    args = dict(args or {})
    if name == "search_terms":
        unknown = sorted(set(args) - {"query"})
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"status": "invalid_input", "message": "검색어(query)가 비어 있습니다. 찾을 내용을 문장으로 넣어 주세요."}
        if search_fn is None:
            from search import search_terms as search_fn
        try:
            res = _compact_search(search_fn(query.strip(), product=PRODUCT, k=k))   # 상품명은 모델이 아니라 코드가 정한다
        except SystemExit as e:                              # vectorstore가 Ollama 연결 실패를 SystemExit으로 알린다
            return {"status": "error", "message": f"검색 실패: {e}"}
        except Exception as e:
            return {"status": "error", "message": f"검색 중 오류: {e}"}
        if unknown:
            res["warnings"] = [f"알 수 없는 인자를 무시했습니다: {', '.join(unknown)}"]
        return res
    if name == "calc_interest":
        unknown = sorted(set(args) - set(CALC_ARGS))
        if unknown:
            return {"status": "invalid_input", "message": f"알 수 없는 인자: {', '.join(unknown)}. "
                                                          f"사용할 수 있는 인자: {', '.join(CALC_ARGS)}"}
        try:
            return calc_interest(**args)
        except Exception as e:                               # 계산 도구는 예외를 내지 않게 만들었지만, 만약을 대비한다
            return {"status": "error", "message": f"계산 중 오류: {e}"}
    return {"status": "unknown_tool", "message": f"알 수 없는 도구: {name}. 사용할 수 있는 도구: {', '.join(TOOL_NAMES)}"}


def ask_and_run(question: str, variant: str = DEFAULT_VARIANT, today: str | None = None, model=None, search_fn=None) -> dict:
    out = ask(question, variant, today, model)
    out["tool_results"] = [{"name": c["name"], "args": c["args"], "result": run_tool(c["name"], c["args"], search_fn)}
                           for c in out["tool_calls"]]
    return out


def main(argv: list[str] | None = None) -> None:
    import argparse
    p = argparse.ArgumentParser(description="질문 하나로 모델의 도구 선택을 확인한다")
    p.add_argument("question")
    p.add_argument("--variant", choices=VARIANTS, default=DEFAULT_VARIANT)
    p.add_argument("--today", default=None, help="오늘 날짜 (기본: 실제 오늘)")
    p.add_argument("--run", action="store_true", help="도구까지 실행")
    a = p.parse_args(argv)
    try:
        out = (ask_and_run if a.run else ask)(a.question, a.variant, a.today)
    except ToolCallingError as e:
        raise SystemExit(str(e))
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
