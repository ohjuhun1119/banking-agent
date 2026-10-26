"""Day 1 (여유 있으면): agent ⇄ tools 반복 구조. 6일차 그래프의 뼈대와 같은 모양이다."""
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition


@tool
def add(a: int, b: int) -> int:
    """두 정수를 더한다. 덧셈이 필요할 때 사용한다."""
    return a + b


tools = [add]


def build_graph(llm):
    llm_with_tools = llm.bind_tools(tools)

    def agent(state: MessagesState) -> dict:
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(tools))      # tool_calls를 읽어 실제 함수를 실행
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)  # 도구 호출이 있으면 tools, 없으면 종료
    builder.add_edge("tools", "agent")              # 결과를 LLM에 다시 전달
    return builder.compile()


if __name__ == "__main__":
    graph = build_graph(ChatOllama(model="qwen2.5:7b", temperature=0))
    result = graph.invoke({"messages": [("user", "1234 더하기 5678은 얼마야?")]})
    for m in result["messages"]:
        print(type(m).__name__, "|", getattr(m, "tool_calls", None) or m.content)
