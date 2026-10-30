"""MCP wrapper for news. Data logic lives in news.py."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from mcp.types import ToolAnnotations

from tools.common import safe_tool
from tools.services import Services

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def tools(svc: Services) -> list[SdkMcpTool[Any]]:
    @tool(
        "get_headlines",
        "Recent headlines from the user's fintech and digital-asset feeds, newest first.",
        {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Keyword filter like stablecoin or Fed. Omit for all.",
                },
                "hours": {"type": "number", "minimum": 1, "maximum": 168, "default": 24},
            },
        },
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_headlines(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.news.get_headlines(args.get("topic"), float(args.get("hours", 24)))

    @tool(
        "get_article_summary",
        "Title, description, and opening text of one article, given its link.",
        {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Article link"}},
            "required": ["url"],
        },
        annotations=READ_ONLY,
    )
    @safe_tool
    async def get_article_summary(args: dict[str, Any]) -> dict[str, Any]:
        return await svc.news.get_article_summary(args["url"])

    return [get_headlines, get_article_summary]
