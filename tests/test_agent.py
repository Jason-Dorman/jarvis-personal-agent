from datetime import date

import pytest

from jarvis.agent import Brain, build_system_prompt, wants_deep
from jarvis.config import get_config, model_id
from tools.registry import SERVERS
from tools.services import Services


@pytest.mark.parametrize(
    ("text", "deep"),
    [
        ("Think hard about USDC", True),
        ("give me a DEEP DIVE on pegs", True),
        ("how's USDC?", False),
        ("I think hardware wallets are fine", False),
    ],
)
def test_wants_deep(text, deep):
    assert wants_deep(text) is deep


def test_model_aliases_resolve_to_exact_ids():
    assert model_id("sonnet") == "claude-sonnet-5-5"
    assert model_id("opus") == "claude-opus-5-5"
    assert model_id("claude-haiku-5-5") == "claude-haiku-5-5"


def test_system_prompt_has_rules_notes_and_brief_order():
    p = build_system_prompt(
        get_config(), "- BTC\n- SPY (stock)", "## RWA\ngrows", date(2026, 10, 29)
    )
    assert "3 short sentences at most" in p
    assert "Every number you say must come from a tool result" in p
    assert "- SPY (stock)" in p and "## RWA" in p
    assert "Thursday, October 29, 2026" in p
    order = [
        "market mood",
        "Watchlist:",
        "Stablecoin pegs",
        "top three",
        "touches one of the user's theses",
    ]
    positions = [p.index(s) for s in order]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_options_turn_off_every_builtin_tool():
    cfg = get_config()
    svc = Services.create(cfg, None)
    try:
        opts = Brain(cfg, None, svc).options()
    finally:
        await svc.aclose()
    assert opts.tools == []
    assert opts.permission_mode == "dontAsk"
    assert opts.setting_sources == []
    assert opts.strict_mcp_config is True
    assert set(opts.mcp_servers) == set(SERVERS)
    assert all(t.startswith("mcp__") for t in opts.allowed_tools)
    assert "mcp__brief__get_brief_data" in opts.allowed_tools
    assert len(opts.allowed_tools) == 11
    assert opts.model == "claude-sonnet-5-5"
    assert opts.include_partial_messages is True
