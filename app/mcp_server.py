"""이자 계산 도구를 MCP 서버(stdio)로 연다. 계산은 app/tools/interest.py의 함수가 하고 여기는 얇은 껍데기다.

실행: 레포 루트에서  python -m app.mcp_server      (pip install -r requirements-mcp.txt 필요)
Claude Desktop 등록 예시는 README의 'MCP 서버' 절.
"""
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from app.tools.interest import calc_interest  # noqa: E402

server = MCPServer("banking-agent-calc")


@server.tool(name="calc_interest", description=(calc_interest.__doc__ or "").strip())
def calc_interest_tool(principal: int | None = None, start_date: str | None = None, maturity_date: str | None = None,
                       termination_date: str | None = None, base_rate: float | None = None) -> dict[str, Any]:
    """모르는 값은 비우고 부르면 `missing_info`로 필요한 값을 알려 준다. 잘못된 입력도 예외가 아니라 `status`로 돌려준다."""
    return calc_interest(principal, start_date, maturity_date, termination_date, base_rate)


if __name__ == "__main__":
    server.run()
