"""MCP wrapper for the morning brief. Data assembly lives in jarvis/brief.py."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from mcp.types import ToolAnnotations

from jarvis.brief import get_brief_data as gather
from tools.common import safe_tool
from tools.services import Services


def tools(svc: Services) -> list[SdkMcpTool[Any]]:
    @tool(
        "get_brief_data",
        "Everything the morning brief needs in one call: watchlist moves, peg status, "
        "top headlines, and the user's theses. Call this only when the user asks for the brief.",
        {"type": "object", "properties": {}},
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    @safe_tool
    async def get_brief_data(args: dict[str, Any]) -> dict[str, Any]:
        return await gather(svc)

    return [get_brief_data]
