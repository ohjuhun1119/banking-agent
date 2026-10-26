"""Day 1: qwen2.5:7b가 Tool Calling을 지원하는지 확인한다.

실행 전: ollama 앱이 켜져 있어야 하고, `ollama pull qwen2.5:7b`가 끝나 있어야 한다.
확인 기준: 도구를 불러야 하는 질문에는 tool_calls가 채워지고,
           도구가 필요 없는 질문에는 tool_calls가 비어 있어야 한다.
"""
from langchain_core.tools import tool
from langchain_ollama import ChatOllama


@tool
def add(a: int, b: int) -> int:
    """두 정수를 더한다. 덧셈이 필요할 때 사용한다."""
    return a + b


llm = ChatOllama(model="qwen2.5:7b", temperature=0)
llm_with_tools = llm.bind_tools([add])

for question in ["1234 더하기 5678은 얼마야?", "안녕, 자기소개 한 줄만 해줘."]:
    ai = llm_with_tools.invoke(question)
    print("질문:", question)
    print("  tool_calls:", ai.tool_calls)
    print("  content   :", ai.content[:60])
