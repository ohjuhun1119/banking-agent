"""Day 1: LangGraph 기초 - State, Node, Edge, 조건부 분기(루프).

LLM 없이 순수 파이썬 함수만으로 그래프의 동작 원리를 확인한다.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    count: int          # 몇 번 반복했는지
    log: list[str]      # 노드가 남기는 기록


def work(state: State) -> dict:
    """노드: State를 읽고, 바꿀 값만 dict로 돌려준다."""
    n = state["count"] + 1
    return {"count": n, "log": state["log"] + [f"work 실행 ({n}번째)"]}


def route(state: State) -> str:
    """조건부 엣지: 다음에 갈 노드 이름(또는 END)을 돌려준다."""
    return "work" if state["count"] < 3 else END


builder = StateGraph(State)
builder.add_node("work", work)
builder.add_edge(START, "work")
# work 다음은 route 결과에 따라 work(반복) 또는 END. 세 번째 인자는 그림에 반복 화살표를 그리기 위한 정보
builder.add_conditional_edges("work", route, {"work": "work", END: END})

graph = builder.compile()

if __name__ == "__main__":
    result = graph.invoke({"count": 0, "log": []})
    print(result["count"])
    for line in result["log"]:
        print(line)
    print(graph.get_graph().draw_mermaid())
