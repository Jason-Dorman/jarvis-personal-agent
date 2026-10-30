"""MCP wrapper for markets. Data logic lives in markets.py."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from mcp.types import ToolAnnotations

from tools.common import safe_tool
from tools.services import Services

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def tools(svc: Services) -> list[SdkMcpTool[Any]]:
    @tool(
        "get_price",
        "Current price and recent change for one crypto coin, stock, ETF, or rate (like ^TNX).",
        {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker, e.g. BTC, SPY, ^TNX"},
                "asset_type": {
                    "type": "string",
                    "enum": ["auto", "crypto", "stock"],
                    "description": "Leave as auto unless the symbol is ambiguous",
                },
            },
            "required": ["symbol"],
        },
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_price(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.markets.get_price(
            args["symbol"], args.get("asset_type", "auto"), svc.watchlist()
        )

    @tool(
        "get_movers",
        "Watchlist symbols that moved more than the user's threshold, biggest first.",
        {"type": "object", "properties": {}},
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_movers(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.markets.get_movers(svc.watchlist(), svc.cfg.brief.move_threshold_pct)

    @tool(
        "get_watchlist_snapshot",
        "Price and change for every symbol on the user's watchlist.",
        {"type": "object", "properties": {}},
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_watchlist_snapshot(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.markets.get_watchlist_snapshot(svc.watchlist())

    return [get_price, get_movers, get_watchlist_snapshot]
