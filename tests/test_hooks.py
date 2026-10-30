import json

import pytest

from jarvis.hooks import ToolHooks, parse_tool_response
from jarvis.memory import Memory
from tools.registry import split_mcp_name


class FakeHud:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def tool_call(self, tool, fn, args=None):
        self.events.append(("tool_call", tool, fn, args))

    async def tool_result(self, tool):
        self.events.append(("tool_result", tool))

    async def card(self, **card):
        self.events.append(("card", card["title"]))


PEG = {
    "symbol": "USDC",
    "name": "USD Coin",
    "price": 0.9991,
    "status": "normal",
    "supply_change_7d_pct": -1.1,
}


def _text(obj) -> list[dict]:
    return [{"type": "text", "text": json.dumps(obj)}]


@pytest.mark.parametrize(
    "response",
    [_text(PEG), {"content": _text(PEG)}, json.dumps(PEG), PEG],
)
def test_parse_tool_response_shapes(response):
    assert parse_tool_response(response) == PEG


def test_parse_tool_response_non_json():
    assert parse_tool_response("oops") == {"raw": "oops"}


def test_split_mcp_name():
    assert split_mcp_name("mcp__pegs__get_peg") == ("pegs", "get_peg")
    assert split_mcp_name("Bash") is None
    assert split_mcp_name("mcp__other__thing") is None


@pytest.mark.asyncio
async def test_hooks_emit_hud_events_log_and_hint():
    hud, mem = FakeHud(), Memory(":memory:")
    hooks = ToolHooks(mem, hud)
    hooks.start_turn()
    base = {
        "tool_name": "mcp__pegs__get_peg",
        "tool_input": {"stablecoin": "USDC"},
        "tool_use_id": "t1",
    }
    assert await hooks.pre(base, "t1", None) == {}
    out = await hooks.post({**base, "tool_response": _text(PEG), "duration_ms": 120}, "t1", None)

    assert hud.events == [
        ("tool_call", "pegs", "get_peg", {"stablecoin": "USDC"}),
        ("card", "USDC peg"),
    ]
    hint = out["hookSpecificOutput"]["additionalContext"]
    assert "USDC peg" in hint and "usdc" in hint
    assert hooks.called == [("pegs", "get_peg")]
    row = mem.conn.execute(
        "SELECT tool, fn, inputs, outputs, error, duration_ms FROM tool_calls"
    ).fetchone()
    assert (row["tool"], row["fn"], row["duration_ms"], row["error"]) == (
        "pegs",
        "get_peg",
        120,
        None,
    )
    assert json.loads(row["outputs"])["price"] == 0.9991


@pytest.mark.asyncio
async def test_error_result_gets_tool_result_and_logged_error():
    hud, mem = FakeHud(), Memory(":memory:")
    hooks = ToolHooks(mem, hud)
    data = {
        "tool_name": "mcp__pegs__get_peg",
        "tool_input": {},
        "tool_response": _text({"error": "unknown"}),
    }
    assert await hooks.post(data, "t1", None) == {}
    assert hud.events == [("tool_result", "pegs")]
    assert mem.conn.execute("SELECT error FROM tool_calls").fetchone()["error"] == "unknown"


@pytest.mark.asyncio
async def test_failure_hook_logs_and_clears_marker():
    hud, mem = FakeHud(), Memory(":memory:")
    hooks = ToolHooks(mem, hud)
    await hooks.failure(
        {"tool_name": "mcp__news__get_headlines", "tool_input": {}, "error": "boom"}, "t", None
    )
    assert hud.events == [("tool_result", "news")]
    assert mem.conn.execute("SELECT error FROM tool_calls").fetchone()["error"] == "boom"


@pytest.mark.asyncio
async def test_hooks_ignore_tools_that_are_not_ours():
    hud = FakeHud()
    hooks = ToolHooks(None, hud)
    assert await hooks.pre({"tool_name": "Bash", "tool_input": {}}, "t", None) == {}
    assert hud.events == [] and hooks.called == []


@pytest.mark.asyncio
async def test_no_hud_means_no_hint():
    hooks = ToolHooks(None, None)
    data = {"tool_name": "mcp__pegs__get_peg", "tool_input": {}, "tool_response": _text(PEG)}
    assert await hooks.post(data, "t", None) == {}
