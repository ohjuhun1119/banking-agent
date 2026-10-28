"""6일차: LangGraph로 agent ⇄ tools 반복을 조립한다.

처리 흐름 (한 질문 = 한 턴)
  START -> agent -> (도구 호출이 있으면) tools -> agent -> ... -> (없으면) END
                 \\-> (호출 횟수가 한도를 넘으면) limit -> END

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
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from app import tool_calling as tc  # noqa: E402
from app.grounding import describe_dropped, ground_calc_args  # noqa: E402
from app.state import AgentState, accepted_values, describe_confirmed  # noqa: E402

MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", "5"))          # 한 질문에서 부를 수 있는 도구 횟수 (노션 오류 처리 표: 예 5회)
TOOL_TIMEOUT_S = float(os.environ.get("TOOL_TIMEOUT_S", "30"))       # 도구 한 번의 대기 시간 (처음 임베딩 모델을 올릴 때를 고려해 넉넉하게)
OLD_TOOL_CHARS = 300                                                 # 이전 질문의 도구 결과는 이 길이로 줄여서 모델에 보낸다 (8192 토큰 안에 들어가게)
RECURSION_LIMIT = 25                                                 # 그래프 단계 수의 안전 한도 (정상 흐름은 13단계 이하)

LIMIT_TEXT = ("도구를 너무 많이 호출해서 여기서 멈췄어요. 질문을 조금 더 구체적으로 다시 알려주시거나, "
              "상담원 연결을 요청해 주세요.")
EMPTY_TEXT = "답변을 만들지 못했어요. 질문을 한 번 더 알려주세요."

# 도구 결과를 받은 뒤의 행동 규칙. 7일차에 근거 표시와 검색 -> 계산 연결을 다듬으면서 고친다.
GRAPH_RULES = """도구 결과를 받으면 status를 보고 다음 행동을 고른다.
- ok (계산): 도구가 준 이자 금액, 계산식, 사용한 가정을 답변에 그대로 쓴다. 금액을 직접 계산하지 않는다.
- need_base_rate: 신규일 당시 홈페이지에 고시된 계약기간별 기본금리(%)를 사용자에게 묻는다. 값을 지어내지 않는다.
- missing_info: 부족한 값만 골라서 묻는다.
- invalid_input: message를 사용자에게 전하고 값을 다시 확인해 달라고 한다.
- not_early_termination: 계산하지 않았다고 알리고 message의 안내를 전한다.
- 약관 검색 결과: 결과에 있는 내용만 답한다. not_found면 '약관에서 확인되지 않습니다'라고 안내한다.
- error: 잠시 후 다시 시도하거나 상담원 연결을 이용해 달라고 안내한다.
- 결과에 dropped_args가 있으면: 그 값은 사용자가 말하지 않아서 계산에 쓰지 않았다는 뜻이다. 계산에 필요한 값이면 사용자에게 묻는다.
같은 도구를 같은 값으로 다시 부르지 않는다. 결과가 나왔으면 그 결과로 답한다."""


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


def make_agent_node(get_model):
    def agent(state: dict) -> dict:
        try:
            ai = get_model().invoke(prompt_messages(state))
        except Exception as e:                                # Ollama 꺼짐, 모델 없음 등
            return _rollback(state, f"모델 호출 실패 ({tc.TOOL_MODEL}): {e}. 'ollama serve'와 'ollama pull {tc.TOOL_MODEL}'를 확인하세요.")
        if not getattr(ai, "tool_calls", None) and not str(ai.content).strip():
            ai = AIMessage(content=EMPTY_TEXT)                # 도구도 답도 없는 빈 응답
        return {"messages": [ai], "model_calls": state.get("model_calls", 0) + 1}
    return agent


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
        out = []
        for call in last.tool_calls:
            name, args = call["name"], dict(call.get("args") or {})
            key, t0 = _args_key(name, args), time.perf_counter()
            duplicate, dropped = key in seen, {}
            if duplicate:                                     # 같은 호출을 또 하는 반복을 끊는다 (한도에는 센다)
                result, retried = {"status": "duplicate", "message": "같은 도구를 같은 값으로 이미 불렀습니다. 앞의 결과로 답하세요."}, False
            else:
                run_args = args
                if name == "calc_interest":                   # 사용자가 말하지 않은 값은 빼고 도구가 되묻게 한다
                    run_args, dropped = ground_calc_args(args, said, _today(state), confirmed)
                result, retried = run_guarded(name, run_args, timeout, search_fn)
                if dropped:
                    result = {**result, "dropped_args": dropped,
                              "note": f"사용자가 말하지 않은 값({describe_dropped(dropped)})은 계산에 쓰지 않았습니다."}
                if name == "calc_interest":
                    confirmed.update(accepted_values(run_args))
            seen.add(key)
            count += 1
            trace.append({"name": name, "args": args, "status": result.get("status"), "ms": int((time.perf_counter() - t0) * 1000),
                          "retried": retried, "duplicate": duplicate, "dropped": dropped})
            out.append(ToolMessage(content=json.dumps(result, ensure_ascii=False, default=str), tool_call_id=call.get("id") or "", name=name))
        return {"messages": out, "tool_calls": count, "trace": trace, "confirmed": confirmed}
    return tools


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
            cache["m"] = tc.get_model(variant or tc.DEFAULT_VARIANT)
        return cache["m"]

    def route(state: dict) -> str:
        if state.get("stop_reason"):
            return END
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        if not calls:
            return END
        return "limit" if state.get("tool_calls", 0) + len(calls) > limit else "tools"

    builder = StateGraph(AgentState)
    builder.add_node("agent", make_agent_node(get_model))
    builder.add_node("tools", make_tools_node(search_fn, tool_timeout))
    builder.add_node("limit", limit_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route, {"tools": "tools", "limit": "limit", END: END})
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
        cfg = {"configurable": {"thread_id": session_id}, "recursion_limit": RECURSION_LIMIT}
        t0 = time.perf_counter()
        # confirmed와 messages는 일부러 넣지 않는다: 체크포인터에 저장된 세션 값이 이어진다
        final = self.graph.invoke({"messages": [HumanMessage(question)], "today": today, "tool_calls": 0, "model_calls": 0,
                                   "trace": [], "stop_reason": None, "error": None}, cfg)
        last = final["messages"][-1] if final.get("messages") else None
        answer = None if final.get("error") else (last.content if isinstance(last, AIMessage) and not last.tool_calls else None)
        confirmed = final.get("confirmed") or {}
        return {"answer": answer, "error": final.get("error"), "stop_reason": final.get("stop_reason"),
                "tool_calls": final.get("tool_calls", 0), "model_calls": final.get("model_calls", 0), "trace": final.get("trace", []),
                "confirmed": confirmed, "confirmed_text": describe_confirmed(confirmed), "session_id": session_id,
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
                 + (f" (말하지 않은 값 제외: {describe_dropped(t['dropped'])})" if t.get("dropped") else ""))
        print(f"  도구: {t['name']} {json.dumps(t['args'], ensure_ascii=False)} -> {t['status']} ({t['ms']}ms){flags}")
    if res["error"]:
        print(f"[오류] {res['error']}")
    else:
        print(res["answer"])
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
