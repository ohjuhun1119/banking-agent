"""6일차: 에이전트 그래프의 상태(State).

한 질문(턴)마다 새로 시작하는 값과, 대화(세션)가 이어지는 동안 남는 값을 나눈다.

  턴마다 초기화 (graph.Agent.ask가 입력으로 덮어쓴다)
    tool_calls   이번 질문에서 부른 도구 횟수 (제한 비교용)
    model_calls  이번 질문에서 모델을 부른 횟수
    trace        이번 질문의 도구 호출 기록 (이름, 인자, status, 시간, 재시도 여부)
    stop_reason  None | "tool_limit" (호출 횟수 초과) | "model_error" (모델 호출 실패)
    error        모델 호출 실패 사유
    today        오늘 날짜 (시스템 프롬프트에 넣는다. 비우면 실제 오늘)
  세션 동안 유지 (체크포인터가 저장)
    messages     대화 전체 (질문, 도구 호출, 도구 결과, 답변)
    confirmed    계산 도구가 받아들인 값: 원금 · 신규일 · 만기일 · 해지일 · 기본금리

confirmed는 '모델이 계산 도구에 넣었고, 도구의 입력 검사를 통과한 값'이다. 사용자가 되물음에 답한 뒤에도
앞에서 받은 값을 잊지 않게 시스템 프롬프트에 요약해서 보여주는 데 쓴다 (graph.system_text).
"""
from datetime import date
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.tool_calling import CALC_ARGS
from app.validators import parse_date, parse_principal, parse_rate, validate_interest_inputs


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    today: str | None
    tool_calls: int
    model_calls: int
    trace: list[dict]
    stop_reason: str | None
    error: str | None
    confirmed: dict


_PARSERS = {"principal": parse_principal, "start_date": parse_date, "maturity_date": parse_date,
            "termination_date": parse_date, "base_rate": parse_rate}


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def accepted_values(args) -> dict:
    """계산 도구에 넘긴 인자 중 입력 검사를 통과한 값만 골라 JSON으로 저장할 수 있는 형태로 돌려준다.

    비어 있는 값, 형식이 틀린 값(원금 '1000만원', 날짜 '20일'), 범위를 벗어난 값(원금 100만 원 미만,
    기본금리 20% 초과, 해지일이 신규일보다 앞섬 등)은 뺀다. 날짜는 YYYY-MM-DD 문자열, 기본금리는 float, 원금은 int.
    """
    if not isinstance(args, dict):
        return {}
    raw = {f: args.get(f) for f in CALC_ARGS}
    bad = {e["field"] for e in validate_interest_inputs(**raw).errors}
    out: dict = {}
    for field, value in raw.items():
        if _blank(value) or field in bad:
            continue
        parsed = _PARSERS[field](value)
        out[field] = parsed.isoformat() if isinstance(parsed, date) else float(parsed) if field == "base_rate" else parsed
    return out


def describe_confirmed(confirmed: dict | None) -> str:
    """확인된 값을 한 줄로 요약한다. 비어 있으면 빈 문자열."""
    c = confirmed or {}
    parts = []
    if "principal" in c:
        parts.append(f"원금 {c['principal']:,}원")
    for key, label in (("start_date", "신규일"), ("maturity_date", "만기일"), ("termination_date", "해지일")):
        if key in c:
            parts.append(f"{label} {c[key]}")
    if "base_rate" in c:
        parts.append(f"기본금리 {c['base_rate']:g}%")
    return " · ".join(parts)
