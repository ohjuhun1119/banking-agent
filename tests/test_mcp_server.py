"""MCP 서버 시험: 이자 계산 도구가 MCP로 열려 있고, 직접 부른 결과와 같은 결과를 돌려주는지. 모델 · 색인은 쓰지 않는다."""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from app.mcp_server import server  # noqa: E402
from app.tools.interest import calc_interest  # noqa: E402

OK = dict(principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-07-20", base_rate=3.5)


def run(coro):
    return asyncio.run(coro)


async def _call(target, args):
    async with Client(target) as c:
        return await c.call_tool("calc_interest", args)


def test_the_server_exposes_only_calc_interest_with_all_inputs_optional():
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    tools = run(go())
    assert [t.name for t in tools] == ["calc_interest"]
    schema = tools[0].input_schema
    assert set(schema["properties"]) == {"principal", "start_date", "maturity_date", "termination_date", "base_rate"}
    assert not schema.get("required")                                                  # 모르는 값은 비우고 부르면 도구가 되묻는다
    assert tools[0].description == calc_interest.__doc__.strip()                       # 에이전트가 보는 설명과 같은 글을 클라이언트도 본다


def test_a_call_returns_exactly_what_the_function_returns():
    res = run(_call(server, OK))
    assert not res.is_error
    assert res.structured_content == calc_interest(**OK) and res.structured_content["status"] == "ok"


@pytest.mark.parametrize("args,status", [({}, "missing_info"), ({**OK, "principal": -1}, "invalid_input"),
                                         ({**OK, "termination_date": "2027-02-01"}, "not_early_termination")])
def test_missing_and_invalid_inputs_come_back_as_a_status_not_a_protocol_error(args, status):
    res = run(_call(server, args))
    assert not res.is_error and res.structured_content["status"] == status


def test_the_real_stdio_process_answers_a_call():
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(ROOT))
    res = run(_call(params, OK))
    assert not res.is_error and res.structured_content == calc_interest(**OK)
