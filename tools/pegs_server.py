"""MCP wrapper for stablecoin pegs. Data logic lives in pegs.py."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from mcp.types import ToolAnnotations

from tools.common import safe_tool
from tools.services import Services

READ_ONLY = ToolAnnotations(readOnlyHint=True)
STABLECOIN = {"type": "string", "description": "Symbol or name, e.g. USDC, USDT, Tether"}


def tools(svc: Services) -> list[SdkMcpTool[Any]]:
    @tool(
        "get_peg",
        "A stablecoin's price, distance from its $1 peg, status, and supply changes.",
        {"type": "object", "properties": {"stablecoin": STABLECOIN}, "required": ["stablecoin"]},
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_peg(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.pegs.get_peg(args["stablecoin"])

    @tool(
        "get_supply_change",
        "How much a stablecoin's circulating supply changed over the last N days.",
        {
            "type": "object",
            "properties": {
                "stablecoin": STABLECOIN,
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 7},
            },
            "required": ["stablecoin"],
        },
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_supply_change(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.pegs.get_supply_change(args["stablecoin"], args.get("days", 7))

    @tool(
        "list_peg_alerts",
        "Large stablecoins that are off their peg right now. Empty means all pegs normal.",
        {"type": "object", "properties": {}},
        annotations=READ_ONLY,
    )
    @safe_tool
    async def list_peg_alerts(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.pegs.list_peg_alerts()

    return [get_peg, get_supply_change, list_peg_alerts]
