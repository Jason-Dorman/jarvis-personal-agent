"""All of Jarvis's MCP servers in one place, and the exact tool names the brain may call.

Each tool is its own in-process server (spec 7, DECISIONS.md). The allow list built here
is the only set of tools the agent can use. Every built-in Agent SDK tool stays off.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server

from tools import brief_server, markets_server, news_server, notes_server, pegs_server
from tools.services import Services

SERVERS: dict[str, Callable[[Services], list[SdkMcpTool[Any]]]] = {
    "markets": markets_server.tools,
    "pegs": pegs_server.tools,
    "news": news_server.tools,
    "notes": notes_server.tools,
    "brief": brief_server.tools,
}

MCP_PREFIX = "mcp__"


def mcp_name(server: str, fn: str) -> str:
    return f"{MCP_PREFIX}{server}__{fn}"


def split_mcp_name(name: str) -> tuple[str, str] | None:
    """`mcp__pegs__get_peg` → ("pegs", "get_peg"). None for anything that isn't ours."""
    if not name.startswith(MCP_PREFIX):
        return None
    server, _, fn = name[len(MCP_PREFIX) :].partition("__")
    if server not in SERVERS or not fn:
        return None
    return server, fn


def build(svc: Services) -> tuple[dict[str, McpSdkServerConfig], list[str]]:
    """(mcp_servers for the agent options, allowed tool names)."""
    servers: dict[str, McpSdkServerConfig] = {}
    allowed: list[str] = []
    for name, make in SERVERS.items():
        tool_list = make(svc)
        servers[name] = create_sdk_mcp_server(name, tools=tool_list)
        allowed += [mcp_name(name, t.name) for t in tool_list]
    return servers, allowed
