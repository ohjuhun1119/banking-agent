"""6일차: LangGraph로 agent ⇄ tools 반복을 조립한다.

처리 흐름 (한 질문 = 한 턴)
  START -> agent -> (도구 호출이 있으면) tools -> agent -> ... -> (없으면) END
                 \\-> (호출 횟수가 한도를 넘으면) limit -> END
  START -> (상담원 연결 요청이거나, 저장 확인에 '네'라고 답하면) handoff -> END     모델을 거치지 않고 request_handoff로 저장한 뒤 코드가 만든 문장으로 답한다

  agent   시스템 프롬프트 + 확인된 값 + 대화를 모델에 보내 '추가 도구 호출 / 되묻기 / 최종 답변' 중 하나를 받는다.
  tools   모델이 고른 도구를 실행하고 결과를 ToolMessage로 돌려준다 (대기 시간 제한, 1회 재시도, 같은 호출 반복 차단).
          계산 도구에는 '사용자가 실제로 말한 값'만 넘긴다: 근거 없는 값은 빼고(app/grounding.py) 도구가 되묻게 한다.
  limit   한 질문에서 도구를 너무 많이 부르려 하면 실행하지 않고 중단 안내를 한다 (상담원 연결 제안).

세션: 체크포인터(MemorySaver)가 session_id(thread_id)별로 대화와 확인된 값을 저장해서, 되묻기 뒤의 답을 이어 받는다.
서버나 CLI 프로세스를 다시 시작하면 저장된 대화는 사라진다 (파일에 저장하는 체크포인터는 필요해지면 교체).

이 단계에서 하지 않는 것 (7~9일차)
  - 검색 결과로 경과 구간을 판단해 되묻기를 결정하는 연결, 답변의 근거 표시 형식 (7일차)
  - 상담 요청 저장 도구와 오류 상황 11가지 재현 (8일차)
  - /chat 엔드포인트 정리, 스트리밍, 트레이싱 (9일차)

실행: 레포 루트에서
  python -m app.graph "1000만원 2026-01-01 신규 2027-01-01 만기 2026-05-31 해지하면 이자는?"
  python -m app.graph --chat                 (한 세션으로 이어서 대화: 되묻기에 답하는 흐름 확인)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import re
import time
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from app import tool_calling as tc  # noqa: E402
from app.answer_check import (claims_handoff_done, foreign_script, handoff_request, is_affirmative, not_found_claim,  # noqa: E402
                              promises_after_tax, tool_syntax, unsupported_phone_numbers, ungrounded_values)
from app.evidence import LOOKUP_QUERY, build_evidence, calc_intent, format_evidence  # noqa: E402
from app.grounding import FIELD_LABELS, describe_dropped, ground_calc_args  # noqa: E402
from app.slots import fill_missing_from_text  # noqa: E402
from app.tools import handoff as hf  # noqa: E402
from app.state import AgentState, accepted_values, describe_confirmed  # noqa: E402

MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", "5"))          # 한 질문에서 부를 수 있는 도구 횟수 (노션 오류 처리 표: 예 5회)
TOOL_TIMEOUT_S = float(os.environ.get("TOOL_TIMEOUT_S", "30"))       # 도구 한 번의 대기 시간 (처음 임베딩 모델을 올릴 때를 고려해 넉넉하게)
OLD_TOOL_CHARS = 300                                                 # 이전 질문의 도구 결과는 이 길이로 줄여서 모델에 보낸다 (8192 토큰 안에 들어가게)
RECURSION_LIMIT = 25                                                 # 그래프 단계 수의 안전 한도 (정상 흐름은 13단계 이하)

HANDOFF_CONFIRM_MARK = "상담 요청을 저장할까요"          # 직전 답변에 이 문장이 있고 사용자가 '네'라고 하면 저장한다
HANDOFF_CONFIRM_TEXT = "상담 요청을 저장할까요? 저장을 원하시면 '네'라고 답해 주세요."
LIMIT_TEXT = ("도구를 너무 많이 호출해서 여기서 멈췄어요. 질문을 조금 더 구체적으로 다시 알려주시거나, " + HANDOFF_CONFIRM_TEXT)
TOOL_ERROR_TEXT = ("도구를 실행하지 못했어요(다시 해 봐도 실패했어요). 잠시 후 다시 시도하시거나 하나은행 고객센터나 영업점에 문의해 주세요. "
                   + HANDOFF_CONFIRM_TEXT)
NO_CONNECT_TEXT = "상담원 연결은 제가 직접 해 드릴 수 없어요. " + HANDOFF_CONFIRM_TEXT
HANDOFF_OK_TEXT = ("상담 요청을 저장했어요. 접수번호는 {ticket}예요. 이 프로그램에는 상담원 연결 기능이 없어서 담당자가 연락드리거나 연결해 주지는 않아요. "
                   "급하시면 하나은행 고객센터나 영업점에 직접 문의해 주세요.")
HANDOFF_FAIL_TEXT = "상담 요청을 저장하지 못했어요. 접수되지 않았으니 하나은행 고객센터나 영업점에 직접 문의해 주세요."
HANDOFF_EMPTY_TEXT = "저장할 질문이 없어요. 상담받고 싶은 내용을 먼저 말씀해 주세요."
EMPTY_TEXT = "답변을 만들지 못했어요. 질문을 한 번 더 알려주세요."
UNSAFE_TEXT = ("확인되지 않은 내용이 섞여 답변을 드리지 못했어요. 질문을 조금 바꿔 다시 물어봐 주시거나, "
               "하나은행 고객센터나 영업점에 문의해 주세요.")
RETRY_NOTES = {
    "language": "직전 답변에 한국어가 아닌 글자(중국어 · 일본어)가 섞였다. 한국어(한글)로만 다시 답한다.",
    "phone": ("직전 답변에 근거 없는 전화번호가 들어 있다. 전화번호는 도구 결과나 사용자가 말한 것만 말한다. "
              "없으면 번호 없이 하나은행 고객센터나 영업점에 문의하라고만 안내한다."),
    "syntax": "직전 답변에 도구 호출 문법이 글로 들어 있다. 도구가 필요하면 직접 호출하고, 아니면 한국어 문장으로만 답한다.",
    "ungrounded": ("직전 답변에 사용자가 말하지 않았고 도구 결과에도 없는 날짜 · 금액({values})이 들어 있다. 그 값은 말하지 않는다 (추측 · 가정도 안 된다). "
                   "사용자가 말한 값과 도구 결과의 값만 쓰고, 모르는 값은 질문한다."),
    "handoff_claim": ("직전 답변이 상담 요청을 접수 · 저장했거나 상담원이 연결된다고 말했다. 저장은 사용자가 확인한 뒤에 시스템이 하고 접수번호도 시스템만 만든다. "
                      "그렇게 말하지 말고 사용자에게 이렇게 물어본다: " + HANDOFF_CONFIRM_TEXT),
    "handoff_confirm": "도구가 시킨 대로 사용자에게 상담 요청을 저장할지 물어본다. 이 문장으로 답한다: " + HANDOFF_CONFIRM_TEXT,
    "not_found": "검색 결과가 있는데 '약관에서 확인되지 않는다'고 했다. 검색 결과에 있는 내용으로 답한다.",
    "aftertax": "직전 답변이 세후 이자를 알려 주겠다고 약속했다. 이 도구는 세전 이자만 계산한다. 세후 금액은 약속하지 말고, 계산하는 이자가 세전이라고 밝힌다.",
}
# 약관 · 이자 질문(예금 관련 낱말이 있는 질문)에 모델이 도구를 하나도 안 부르고 답하려 하면, 코드가 먼저 약관 검색을 실행해 근거로 준다
DOMAIN_WORDS = re.compile(r"해지|해약|중도|만기|이자|이율|금리|약관|원금|예금|예치|가입|보호|과세|세금|재예치|인출|일부")

# 도구 결과를 받은 뒤의 행동 규칙. 7일차에 근거 표시와 검색 -> 계산 연결을 다듬으면서 고친다.
GRAPH_RULES = """도구 결과를 받으면 status를 보고 다음 행동을 고른다.
- ok (계산): 도구가 준 이자 금액, 계산식, 사용한 가정을 답변에 그대로 쓴다. 금액을 직접 계산하지 않는다.
- need_base_rate: 신규일 당시 홈페이지에 고시된 계약기간별 기본금리(%)가 얼마였는지 사용자에게 질문한다. 값을 지어내지 않는다.
- missing_info: 부족한 값만 골라서 질문한다.
- invalid_input: message를 사용자에게 전하고 값을 다시 확인해 달라고 한다.
- not_early_termination: 계산하지 않았다고 알리고 message의 안내를 전한다.
- 약관 검색 결과: 결과에 있는 내용만 답한다. not_found면 '약관에서 확인되지 않습니다'라고 안내한다.
- error: 잠시 후 다시 시도하거나 하나은행 고객센터나 영업점에 문의해 달라고 안내한다.
- need_base_rate, missing_info, invalid_input이면 설명만 하고 끝내지 말고, 필요한 값을 알려 달라는 질문으로 끝낸다.
- 결과에 '사용자가 말하지 않은 값은 계산에 쓰지 않았다'는 메모가 있으면: 그 칸의 값은 사용자가 말하지 않은 것이다. 그 값을 추측해서 말하지 말고, 필요하면 사용자에게 묻는다.
- 결과에 '사용자가 말한 값을 질문에서 찾아 채웠다'는 메모가 있으면: 그 값은 사용자가 말한 값이다.
- 사용자가 말하지 않은 날짜 · 금액은 답변에 쓰지 않는다 (추측 · 가정 · 예시도 안 된다).
- 약관에서 확인되지 않습니다는 약관 검색 결과가 not_found일 때만 쓴다. 약관 질문은 먼저 검색 도구로 확인한다.
- needs_confirmation (request_handoff): message가 시키는 문장으로 사용자에게 상담 요청을 저장할지 묻는다. 저장됐다 · 접수됐다 · 연결해 준다고 말하지 않는다.
같은 도구를 같은 값으로 다시 부르지 않는다. 결과가 나왔으면 그 결과로 답한다.
한국어로만 답한다. 중국어 · 일본어 · 영어를 섞지 않는다.
너는 『하나의 정기예금』 약관 안내와 중도해지 세전 이자 계산만 할 수 있다. 세후 이자(세금을 뺀 금액)는 계산하지 못하므로 약속하지 않는다. 계좌 조회 · 해지 처리 같은 실제 업무는 할 수 없다고 말한다. 상담원 연결도 직접 할 수 없다: 사용자가 사람과 얘기하고 싶다고 하면 request_handoff 도구를 부르고(저장하지 않고 저장해도 되는지 물어볼 문장을 돌려준다) 도구가 준 문장대로 묻는다. 일부해지 질문은 약관 안내만 하고 계산하지 않으며, 끝에 '상담 요청을 저장할까요?'라고 제안한다. 세금 · 세후 질문은 세전 이자만 계산하고 기본약관 제9조 ⑤(원천징수 후 금액) 근거를 안내한다. 전화번호나 주소는 도구 결과나 사용자가 말한 것만 말하고, 없으면 만들지 않는다."""


# ---------- 프롬프트 ----------
def system_text(state: dict) -> str:
    """시스템 프롬프트: 도구 선택 규칙(5일차와 같음) + 결과 처리 규칙 + 지금까지 확인된 값."""
    text = tc.SYSTEM_PROMPT.format(today=state.get("today") or date.today().isoformat()) + "\n\n" + GRAPH_RULES
    known = describe_confirmed(state.get("confirmed"))
    if known:
        text += (f"\n\n[지금까지 확인된 값] {known}\n사용자가 바꾸겠다고 하지 않는 한 이 값을 계산 도구에 그대로 넣어도 된다. "
                 "새로운 계산을 시작한다고 하면 이 값은 쓰지 않는다.")
    return text


def _turn_start(messages: list) -> int:
    """이번 질문이 시작된 위치 (마지막 사용자 메시지)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return 0


def prompt_messages(state: dict) -> list:
    """모델에 보낼 메시지. 이전 질문의 긴 도구 결과(약관 본문 등)는 줄이고, 이번 질문의 결과는 그대로 보낸다."""
    msgs = state["messages"]
    start = _turn_start(msgs)
    out: list = [SystemMessage(content=system_text(state))]
    for i, m in enumerate(msgs):
        if isinstance(m, ToolMessage) and i < start and len(str(m.content)) > OLD_TOOL_CHARS:
            m = ToolMessage(content=str(m.content)[:OLD_TOOL_CHARS] + " …(이전 질문의 도구 결과, 길어서 줄임)",
                            tool_call_id=m.tool_call_id, name=m.name)
        out.append(m)
    return out


# ---------- 도구 실행 (대기 시간 제한 + 1회 재시도) ----------
def _call_with_timeout(fn, timeout: float):
    """fn()을 별도 스레드에서 돌려 timeout초 안에 끝나면 (값, False), 아니면 (None, True)를 돌려준다.
    시간이 지난 스레드는 멈출 수 없어서 백그라운드에서 끝날 때까지 두고 결과만 버린다 (daemon이라 종료를 막지 않는다)."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as e:                                # run_tool이 이미 감싸지만, 어떤 예외도 스레드 밖으로 새지 않게 한다
            box["value"] = {"status": "error", "message": f"도구 실행 중 오류: {e}"}

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    return (None, True) if t.is_alive() else (box["value"], False)


def run_guarded(name: str, args: dict, timeout: float | None = None, search_fn=None) -> tuple[dict, bool]:
    """도구를 실행한다. 시간 초과나 status=error면 한 번 더 시도하고, 그래도 실패하면 실패 결과를 돌려준다.
    입력 오류(invalid_input, missing_info 등)는 재시도해도 같으므로 다시 시도하지 않는다. 반환: (결과, 재시도 여부)"""
    timeout = TOOL_TIMEOUT_S if timeout is None else timeout
    retried = False
    result: dict = {}
    for attempt in (1, 2):
        value, timed_out = _call_with_timeout(lambda: tc.run_tool(name, args, search_fn), timeout)
        result = ({"status": "error", "reason": "timeout", "message": f"도구 응답이 {timeout:g}초 안에 오지 않았습니다 (시간 초과)."}
                  if timed_out else value)
        if result.get("status") != "error":
            break
        if attempt == 1:
            retried = True
    if retried and result.get("status") == "error":
        result = {**result, "attempts": 2}
    return result, retried


def _args_key(name: str, args: dict) -> tuple:
    return name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


# ---------- 노드 ----------
def _rollback(state: dict, reason: str) -> dict:
    """모델 호출이 실패하면 이번 질문에서 쌓인 메시지를 모두 지워, 다음에 다시 물어도 대화가 꼬이지 않게 한다."""
    msgs = state["messages"]
    start = _turn_start(msgs)
    return {"messages": [RemoveMessage(id=m.id) for m in msgs[start:] if m.id], "stop_reason": "model_error", "error": reason,
            "model_calls": state.get("model_calls", 0) + 1}


def _turn_tool_results(state: dict) -> list[tuple[str, dict]]:
    """이번 질문에서 나온 도구 결과 [(도구 이름, 결과 dict)]."""
    msgs = state["messages"]
    out = []
    for m in msgs[_turn_start(msgs):]:
        if isinstance(m, ToolMessage):
            try:
                out.append((m.name, json.loads(m.content)))
            except (TypeError, ValueError):
                out.append((m.name, {}))
    return out


def _allowed_texts(state: dict, whole_conversation: bool) -> list[str]:
    """답변이 말해도 되는 근거 글: 사용자 말 + 도구 결과(+ 확인된 값). 전화번호는 이번 질문의 도구 결과만, 날짜 · 금액은 대화 전체의 도구 결과를 허용한다."""
    msgs = state["messages"]
    start = _turn_start(msgs)
    texts = [m.content for i, m in enumerate(msgs) if isinstance(m, HumanMessage) or (isinstance(m, ToolMessage) and (whole_conversation or i >= start))]
    texts.append(" ".join(str(v) for v in (state.get("confirmed") or {}).values()))
    return texts


def check_reply(ai, state: dict) -> tuple[str | None, str]:
    """모델 응답의 문제를 찾는다. 반환: (문제, 설명). 문제: 'language'(한자 · 가나) / 'syntax'(도구 호출 문법이 글로 나옴) /
    'phone'(근거 없는 전화번호) / 'aftertax'(세후 이자를 알려 주겠다고 약속) / 'ungrounded'(근거 없는 날짜 · 금액) / 'not_found'(검색 결과가 있는데 '확인되지 않는다'고 함) / None.
    도구 호출이면 검색어만, 최종 답변이면 본문 전체를 본다."""
    calls = getattr(ai, "tool_calls", None) or []
    if calls:
        bad = any(foreign_script((c.get("args") or {}).get("query")) for c in calls if c.get("name") == "search_terms")
        return ("language", "") if bad else (None, "")
    text = str(ai.content)
    if foreign_script(text):
        return "language", ""
    if tool_syntax(text):
        return "syntax", ""
    if unsupported_phone_numbers(text, *_allowed_texts(state, whole_conversation=False)):
        return "phone", ""
    if promises_after_tax(text):
        return "aftertax", ""
    if claims_handoff_done(text):                             # 접수 · 연결 주장은 모델이 아니라 코드가 저장한 뒤에만 할 수 있다
        return "handoff_claim", ""
    if any(r.get("status") == "needs_confirmation" for _, r in _turn_tool_results(state)) and HANDOFF_CONFIRM_MARK not in text:
        return "handoff_confirm", ""                          # 도구가 시킨 '저장할까요?' 질문이 사용자에게 가야 다음 턴에 '네'를 알아듣는다
    extra = ungrounded_values(text, _allowed_texts(state, whole_conversation=True), _today(state))
    if extra:
        return "ungrounded", ", ".join(extra)
    results = _turn_tool_results(state)
    if not_found_claim(text) and any(n == "search_terms" for n, _ in results) and not any(r.get("status") == "not_found" for n, r in results):
        return "not_found", ""
    return None, ""


def fallback_text(state: dict, problem: str | None = None) -> str:
    """점검을 통과하지 못한 답변 대신 보낼 문구: 도구가 사용자에게 값을 물어보는 중이었다면 그 도구의 질문을, 아니면 고정 문구를."""
    for name, result in reversed(_turn_tool_results(state)):
        if result.get("status") == "needs_confirmation":
            return HANDOFF_CONFIRM_TEXT
        if result.get("status") in ("missing_info", "need_base_rate") and isinstance(result.get("message"), str):
            return result["message"]
    if problem and problem.startswith("handoff"):
        return NO_CONNECT_TEXT
    return UNSAFE_TEXT


ASK_STATUSES = ("missing_info", "need_base_rate")        # 계산 도구가 사용자에게 값을 되묻는 상태


def _filled_text(filled: dict) -> str:
    """코드가 질문에서 읽어 채운 값을 한 줄로 (칸 이름: 값). 금액 · 금리는 읽기 쉽게."""
    parts = []
    for label, v in filled.items():
        if label == FIELD_LABELS["principal"] and isinstance(v, (int, float)):
            v = f"{int(v):,}원"
        elif label == FIELD_LABELS["base_rate"] and isinstance(v, (int, float)):
            v = f"연 {v}%"
        parts.append(f"{label} {v}")
    return ", ".join(parts)


def verbatim_question(state: dict) -> str | None:
    """코드가 먼저 부른 계산이 값을 되묻는 경우에는 모델을 거치지 않고 도구의 질문을 그대로 답한다 (모델 호출 0회).
    모델이 도구를 고르지 않아 코드가 대신 부른 경로라서 문장도 코드가 맡는다. 모델이 문장을 다시 쓰다 뜻을 뒤집은 적이 있다
    (검증용 H19: 도구는 '값이 없습니다', 모델은 '값이 있습니다'). 코드가 질문에서 채운 값은 덧붙여 무엇을 가정했는지 보이게 한다.
    모델이 직접 부른 계산과 ok · not_early_termination · invalid_input 결과는 이전처럼 모델이 답한다."""
    msgs, trace = state.get("messages") or [], state.get("trace") or []
    if not msgs or not isinstance(msgs[-1], ToolMessage) or not trace:
        return None
    t = trace[-1]
    if not (t.get("forced") and t.get("forced_kind") == "calc" and t.get("status") in ASK_STATUSES):
        return None
    results = _turn_tool_results(state)
    name, result = results[-1] if results else ("", {})
    message = result.get("message")
    if name != "calc_interest" or not isinstance(message, str) or not message.strip():
        return None
    filled = result.get("filled_args") or {}
    return message.strip() + (f" (질문에서 읽은 값: {_filled_text(filled)})" if filled else "")


def tool_error_unreported(ai, state: dict) -> bool:
    """도구가 (재시도 뒤에도) 실패했는데 최종 답변이 그 사실을 알리지 않는다: 코드가 실패 안내로 바꾼다 (오류 처리 표: 1회 재시도 후 실패 안내)."""
    if getattr(ai, "tool_calls", None):
        return False
    return any(r.get("status") == "error" for _, r in _turn_tool_results(state)) and not re.search(r"다시 시도|문의", str(ai.content))


def forced_tool(ai, state: dict) -> str | None:
    """도구를 하나도 안 부르고 끝내려는 답에 코드가 먼저 부를 도구: 'calc' / 'search' / None.
    예금 관련 낱말이 있거나 '약관에서 확인되지 않는다'는 말이면 검색이 먼저인데, 이자가 얼마인지 묻는 질문(calc_intent)은 약관 구간표를 나열하는 검색 대신
    계산 도구를 먼저 불러 필요한 값을 되묻게 한다 (검증용 H19: 만기 한 달 전 해지인데 '1개월 미만 0.10%'부터 나열)."""
    msgs = state["messages"]
    if getattr(ai, "tool_calls", None) or any(isinstance(m, ToolMessage) for m in msgs[_turn_start(msgs):]):
        return None
    question = str(msgs[_turn_start(msgs)].content) if msgs else ""
    if not (DOMAIN_WORDS.search(question) or not_found_claim(str(ai.content))):
        return None
    return "calc" if calc_intent(question) else "search"


def needs_forced_search(ai, state: dict) -> bool:
    return forced_tool(ai, state) is not None


def forced_search_message(state: dict) -> AIMessage:
    msgs = state["messages"]
    question = str(msgs[_turn_start(msgs)].content).strip()
    return AIMessage(content="", tool_calls=[{"name": "search_terms", "args": {"query": question}, "id": f"forced-{uuid.uuid4().hex[:12]}", "type": "tool_call"}],
                     additional_kwargs={"forced_search": True})


def forced_calc_message(state: dict) -> AIMessage:
    """값 없이 계산 도구를 부른다: 도구 실행 단계가 질문에서 읽을 수 있는 값을 채우고, 부족한 값은 도구가 되묻는다."""
    return AIMessage(content="", tool_calls=[{"name": "calc_interest", "args": {}, "id": f"forced-{uuid.uuid4().hex[:12]}", "type": "tool_call"}],
                     additional_kwargs={"forced_calc": True})


def make_agent_node(get_model):
    def agent(state: dict) -> dict:
        calls = 0
        asked = verbatim_question(state)
        if asked is not None:                                 # 코드가 먼저 부른 계산이 값을 되묻는다: 도구의 질문 그대로, 모델 호출 없음
            return {"messages": [AIMessage(content=asked)], "model_calls": state.get("model_calls", 0)}
        try:
            msgs = prompt_messages(state)
            model = get_model()
            ai, calls = model.invoke(msgs), 1
            problem, detail = check_reply(ai, state)
            if problem:                                       # 한 번만 다시 시키고, 그래도 문제면 고정 문구로 바꾼다
                note = RETRY_NOTES[problem].format(values=detail)
                ai, calls = model.invoke(msgs + [SystemMessage(content=note)]), 2
                second = check_reply(ai, state)[0]
                if second:
                    ai = AIMessage(content=fallback_text(state, second))
            if tool_error_unreported(ai, state):
                ai = AIMessage(content=TOOL_ERROR_TEXT)
            kind = forced_tool(ai, state)                     # 도구 없이 답하려 하면 코드가 먼저 도구를 부른다
            if kind == "calc":
                ai = forced_calc_message(state)
            elif kind == "search":
                ai = forced_search_message(state)
        except Exception as e:                                # Ollama 꺼짐, 모델 없음 등
            return _rollback(state, f"모델 호출 실패 ({tc.TOOL_MODEL}): {e}. 'ollama serve'와 'ollama pull {tc.TOOL_MODEL}'를 확인하세요.")
        if not getattr(ai, "tool_calls", None) and not str(ai.content).strip():
            ai = AIMessage(content=EMPTY_TEXT)                # 도구도 답도 없는 빈 응답
        return {"messages": [ai], "model_calls": state.get("model_calls", 0) + calls}
    return agent


def handoff_intent(messages: list) -> bool:
    """이번 질문이 상담 요청 저장 요청인가: 키워드(상담원 · 직원 연결 등)이거나, 직전 답변이 '상담 요청을 저장할까요?'였고 사용자가 '네'라고 답했을 때."""
    last = messages[-1] if messages else None
    if not isinstance(last, HumanMessage):
        return False
    if handoff_request(last.content):
        return True
    prev = next((m for m in reversed(messages[:-1]) if isinstance(m, AIMessage) and not m.tool_calls), None)
    return prev is not None and HANDOFF_CONFIRM_MARK in str(prev.content) and is_affirmative(last.content)


def handoff_node(state: dict) -> dict:
    """상담 요청 저장: 질문과 확인된 값을 SQLite에 저장하고, 저장에 성공했을 때만 접수번호를 말한다. 문장은 코드가 만든다 (모델 호출 0회)."""
    humans = [str(m.content) for m in state["messages"] if isinstance(m, HumanMessage)]
    useful = [t for t in humans if not (handoff_request(t) and len(t) <= 20) and not is_affirmative(t)] or humans[-1:]    # '상담원 연결해줘' · '네'만으로는 요약이 안 된다
    result = hf.request_handoff(useful, describe_confirmed(state.get("confirmed")), state.get("session_id") or "")
    trace = list(state.get("trace") or []) + [{"name": tc.HANDOFF_NAME, "args": {}, "status": result["status"], "ms": 0, "retried": False, "duplicate": False,
                                              "dropped": {}, "filled": {}, "ignored": [], "forced": True, "forced_kind": "handoff"}]
    if result["status"] == "saved":
        text, stop = HANDOFF_OK_TEXT.format(ticket=result["ticket_id"]), "handoff_saved"
    elif result["status"] == "invalid_input":
        text, stop = HANDOFF_EMPTY_TEXT, "handoff_failed"
    else:
        text, stop = HANDOFF_FAIL_TEXT, "handoff_failed"
    return {"messages": [AIMessage(content=text)], "stop_reason": stop, "trace": trace, "tool_calls": state.get("tool_calls", 0) + 1}


def _today(state: dict) -> date:
    return date.fromisoformat(state["today"]) if state.get("today") else date.today()


def make_tools_node(search_fn, timeout):
    def tools(state: dict) -> dict:
        last = state["messages"][-1]
        said = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]       # 대화에서 사용자가 한 말 (앞 질문 포함)
        count = state.get("tool_calls", 0)
        trace = list(state.get("trace") or [])
        confirmed = dict(state.get("confirmed") or {})
        seen = {_args_key(t["name"], t["args"]) for t in trace}
        kw = getattr(last, "additional_kwargs", None) or {}
        forced_kind = "search" if kw.get("forced_search") else "calc" if kw.get("forced_calc") else None
        forced = forced_kind is not None
        out, final_args = [], {}
        for call in last.tool_calls:
            name, args = call["name"], dict(call.get("args") or {})
            key, t0 = _args_key(name, args), time.perf_counter()
            duplicate, dropped, filled, ignored = key in seen, {}, {}, []
            if duplicate:                                     # 같은 호출을 또 하는 반복을 끊는다 (한도에는 센다)
                result, retried = {"status": "duplicate", "message": "같은 도구를 같은 값으로 이미 불렀습니다. 앞의 결과로 답하세요."}, False
            elif name == tc.HANDOFF_NAME:                     # 모델이 부른 상담 요청: 저장하지 않는다. 사용자가 직접 요청했거나 '네'라고 확인한 턴에만 handoff 노드가 저장한다
                result, retried = {"status": "needs_confirmation", "message": "사용자가 상담원 연결을 직접 요청했는지 확인되지 않아 저장하지 않았습니다. "
                                                                              "사용자에게 이 문장으로 물어보세요: " + HANDOFF_CONFIRM_TEXT}, False
            else:
                run_args = args
                if name == "calc_interest":                   # 말하지 않은 값은 빼고, 말했는데 모델이 빠뜨린 칸은 채운다
                    ignored = sorted(set(args) - set(tc.CALC_ARGS))      # 도구에 없는 인자 이름(프롬프트의 낱말을 베낀 것 등)은 무시한다
                    run_args, dropped = ground_calc_args({k: v for k, v in args.items() if k in tc.CALC_ARGS}, said, _today(state), confirmed)
                    run_args, filled = fill_missing_from_text(run_args, said, _today(state))
                    final_args[call.get("id")] = run_args
                result, retried = run_guarded(name, run_args, timeout, search_fn)
                notes = []
                if dropped:                                   # 값은 말하지 않는다: 모델이 지어낸 값을 따라 말하지 않게 칸 이름만 알린다
                    result = {**result, "dropped_args": [FIELD_LABELS.get(k, k) for k in dropped]}
                    notes.append(f"사용자가 말하지 않은 값({describe_dropped(dropped)})은 계산에 쓰지 않았습니다.")
                if filled:
                    result = {**result, "filled_args": {FIELD_LABELS.get(k, k): v for k, v in filled.items()}}
                    notes.append(f"사용자가 말한 값({describe_dropped(filled)})을 질문에서 찾아 채웠습니다.")
                if notes:
                    result = {**result, "note": " ".join(notes)}
                if name == "calc_interest":
                    confirmed.update(accepted_values(run_args))
            seen.add(key)
            count += 1
            trace.append({"name": name, "args": args, "status": result.get("status"), "ms": int((time.perf_counter() - t0) * 1000),
                          "retried": retried, "duplicate": duplicate, "dropped": dropped, "filled": filled, "ignored": ignored, "forced": forced, "forced_kind": forced_kind})
            out.append(ToolMessage(content=json.dumps(result, ensure_ascii=False, default=str), tool_call_id=call.get("id") or "", name=name))
        messages = out
        if final_args:                                        # 모델의 호출 기록에도 지어낸 값이 남지 않게, 실제로 쓴 값으로 바꿔 둔다 (같은 id로 교체)
            fixed = [{**c, "args": final_args[c.get("id")]} if c.get("id") in final_args else c for c in last.tool_calls]
            messages = [AIMessage(content=last.content, tool_calls=fixed, id=last.id, additional_kwargs=last.additional_kwargs)] + out
        return {"messages": messages, "tool_calls": count, "trace": trace, "confirmed": confirmed}
    return tools


def make_evidence_node(search_fn, timeout):
    def evidence(state: dict) -> dict:
        """최종 답변이 나간 뒤 코드가 근거 카드를 만든다 (모델은 관여하지 않는다). 점검에 걸려 대체된 답변에는 붙이지 않는다."""
        last = state["messages"][-1]
        if str(getattr(last, "content", "")) in (UNSAFE_TEXT, EMPTY_TEXT, TOOL_ERROR_TEXT, NO_CONNECT_TEXT):
            return {"evidence": []}

        def lookup():
            result, _ = run_guarded("search_terms", {"query": LOOKUP_QUERY}, timeout, search_fn)
            return result
        return {"evidence": build_evidence(_turn_tool_results(state), lookup)}
    return evidence


def limit_node(state: dict) -> dict:
    """도구 호출 한도를 넘었다: 부르려던 도구는 실행하지 않고(건너뜀으로 응답), 중단 안내를 남긴다."""
    skipped = [ToolMessage(content=json.dumps({"status": "skipped", "message": "도구 호출 횟수 제한 때문에 실행하지 않았습니다."}, ensure_ascii=False),
                           tool_call_id=c.get("id") or "", name=c["name"]) for c in state["messages"][-1].tool_calls]
    return {"messages": skipped + [AIMessage(content=LIMIT_TEXT)], "stop_reason": "tool_limit"}


def build_graph(model=None, search_fn=None, tool_timeout: float | None = None, max_tool_calls: int | None = None,
                variant: str | None = None, checkpointer=None):
    """agent ⇄ tools 그래프. model은 invoke(messages)를 가진 객체 (시험용 가짜 모델), 없으면 첫 호출 때 ChatOllama를 만든다."""
    cache: dict = {}
    limit = MAX_TOOL_CALLS if max_tool_calls is None else max_tool_calls

    def get_model():
        if model is not None:
            return model
        if "m" not in cache:
            cache["m"] = tc.get_model(variant or tc.DEFAULT_VARIANT, extra_tools=(tc.HANDOFF_SPEC,))
        return cache["m"]

    def route(state: dict) -> str:
        if state.get("stop_reason"):
            return END
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        if not calls:
            return "evidence"                                 # 최종 답변: 코드가 근거 카드를 붙인다
        return "limit" if state.get("tool_calls", 0) + len(calls) > limit else "tools"

    builder = StateGraph(AgentState)
    builder.add_node("agent", make_agent_node(get_model))
    builder.add_node("tools", make_tools_node(search_fn, tool_timeout))
    builder.add_node("limit", limit_node)
    builder.add_node("evidence", make_evidence_node(search_fn, tool_timeout))
    def entry(state: dict) -> str:                            # 이번 질문이 상담원 연결 요청이면 모델을 거치지 않는다
        return "handoff" if handoff_intent(state["messages"]) else "agent"

    builder.add_node("handoff", handoff_node)
    builder.add_conditional_edges(START, entry, {"agent": "agent", "handoff": "handoff"})
    builder.add_edge("handoff", END)
    builder.add_conditional_edges("agent", route, {"tools": "tools", "limit": "limit", "evidence": "evidence", END: END})
    builder.add_edge("evidence", END)
    builder.add_edge("tools", "agent")                        # 결과를 모델에 다시 전달
    builder.add_edge("limit", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


class Agent:
    """세션별로 대화를 이어 가는 에이전트. ask()가 한 질문을 처리하고 결과를 dict로 돌려준다."""

    def __init__(self, checkpointer=None, **kwargs):
        self.checkpointer = checkpointer or MemorySaver()
        self.graph = build_graph(checkpointer=self.checkpointer, **kwargs)

    def ask(self, question: str, session_id: str = "default", today: str | None = None) -> dict:
        if today:
            date.fromisoformat(today)                         # 잘못된 날짜는 여기서 ValueError
        cfg = {"configurable": {"thread_id": session_id}, "recursion_limit": RECURSION_LIMIT,
               "run_name": "banking-agent-turn", "tags": ["banking-agent"], "metadata": {"session_id": session_id}}      # LANGSMITH_TRACING=true일 때 구분용 (꺼져 있으면 무시)
        t0 = time.perf_counter()
        # confirmed와 messages는 일부러 넣지 않는다: 체크포인터에 저장된 세션 값이 이어진다
        final = self.graph.invoke({"messages": [HumanMessage(question)], "today": today, "tool_calls": 0, "model_calls": 0,
                                   "trace": [], "stop_reason": None, "error": None, "evidence": [], "session_id": session_id}, cfg)
        last = final["messages"][-1] if final.get("messages") else None
        answer = None if final.get("error") else (last.content if isinstance(last, AIMessage) and not last.tool_calls else None)
        confirmed = final.get("confirmed") or {}
        return {"answer": answer, "error": final.get("error"), "stop_reason": final.get("stop_reason"),
                "tool_calls": final.get("tool_calls", 0), "model_calls": final.get("model_calls", 0), "trace": final.get("trace", []),
                "confirmed": confirmed, "confirmed_text": describe_confirmed(confirmed), "session_id": session_id,
                "evidence": [] if final.get("error") else final.get("evidence") or [],
                "evidence_text": "" if final.get("error") else format_evidence(final.get("evidence") or []),
                "ms": int((time.perf_counter() - t0) * 1000)}

    def reset(self, session_id: str = "default") -> None:
        self.checkpointer.delete_thread(session_id)

    def history(self, session_id: str = "default") -> list:
        snap = self.graph.get_state({"configurable": {"thread_id": session_id}})
        return list((snap.values or {}).get("messages", []))


# ---------- 명령줄 ----------
def _print_result(res: dict) -> None:
    for t in res["trace"]:
        flags = ((" (같은 호출 반복, 실행 안 함)" if t["duplicate"] else "") + (" (재시도함)" if t["retried"] else "")
                 + (f" (말하지 않은 값 제외: {describe_dropped(t['dropped'])})" if t.get("dropped") else "")
                 + (f" (질문에서 채움: {describe_dropped(t['filled'])})" if t.get("filled") else "") + ((" (코드가 먼저 계산 호출)" if t.get("forced_kind") == "calc" else " (코드가 먼저 검색)") if t.get("forced") else ""))
        print(f"  도구: {t['name']} {json.dumps(t['args'], ensure_ascii=False)} -> {t['status']} ({t['ms']}ms){flags}")
    if res["error"]:
        print(f"[오류] {res['error']}")
    else:
        print(res["answer"])
        if res.get("evidence_text"):
            print("\n" + res["evidence_text"])
    note = f"  (도구 {res['tool_calls']}회 · 모델 {res['model_calls']}회 · {res['ms']}ms"
    note += f" · 중단: {res['stop_reason']}" if res["stop_reason"] else ""
    print(note + ")")
    if res["confirmed_text"]:
        print(f"  확인된 값: {res['confirmed_text']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="에이전트에게 질문한다 (모델: Ollama)")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--chat", action="store_true", help="한 세션으로 이어서 대화 (빈 줄이나 exit로 종료, /new로 새 대화)")
    ap.add_argument("--session", default="cli")
    ap.add_argument("--today", default=None, help="오늘 날짜 YYYY-MM-DD (기본: 실제 오늘)")
    ap.add_argument("--variant", choices=tc.VARIANTS, default=tc.DEFAULT_VARIANT)
    a = ap.parse_args(argv)
    if not a.question and not a.chat:
        ap.error("질문을 쓰거나 --chat을 붙이세요")
    agent = Agent(variant=a.variant)
    if a.question:
        _print_result(agent.ask(a.question, a.session, a.today))
    if a.chat:
        while True:
            try:
                q = input("질문> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q in ("", "exit", "quit"):
                break
            if q == "/new":
                agent.reset(a.session)
                print("새 대화를 시작합니다.")
                continue
            _print_result(agent.ask(q, a.session, a.today))


if __name__ == "__main__":
    main()
