import asyncio
from datetime import date

import pytest
from claude_agent_sdk import ResultMessage

from jarvis.agent import Brain, build_system_prompt, greeting_reply, wants_deep
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
    assert "Hard limit: 5 sentences" in p
    assert "Don't guess at causes" in p
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


class WindingDownClient:
    """Stands in for ClaudeSDKClient: the cut-off reply takes `drain_s` to finish."""

    def __init__(self, drain_s: float) -> None:
        self.drain_s = drain_s
        self.calls: list[str] = []

    async def interrupt(self):
        self.calls.append("interrupt")

    async def query(self, text):
        self.calls.append(f"query:{text}")

    async def receive_response(self):
        await asyncio.sleep(self.drain_s)
        self.calls.append("drained")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        )


async def test_interrupt_winds_down_in_the_background_and_next_question_waits():
    cfg = get_config()
    svc = Services.create(cfg, None)
    try:
        brain = Brain(cfg, None, svc)
        brain._client = client = WindingDownClient(drain_s=0.2)
        brain._in_flight = True
        await asyncio.wait_for(brain.interrupt(), 0.05)  # returns straight away
        assert brain.hooks.muted  # the old turn's late cards stay off the screen
        async for _ in brain.ask("and tether?"):
            pass
    finally:
        await svc.aclose()
    assert client.calls[:3] == ["interrupt", "drained", "query:and tether?"]
    assert not brain.hooks.muted


@pytest.mark.parametrize(
    ("heard", "reply"),
    [
        ("Good morning.", "Good morning, Mr. Dorman."),
        ("good morning, Jarvis!", "Good morning, Mr. Dorman."),
        ("Hey Jarvis, good evening", "Good evening, Mr. Dorman."),
        ("Morning.", "Good morning, Mr. Dorman."),
        ("Good morning, what's bitcoin at?", None),  # a question goes to Claude
        ("Give me my morning brief.", None),
        ("How was the morning session?", None),
    ],
)
def test_greeting_reply(heard, reply):
    assert greeting_reply(heard, "Mr. Dorman") == reply


def test_greeting_without_a_name():
    assert greeting_reply("good afternoon", None) == "Good afternoon."


def test_prompt_tells_the_brain_how_to_greet():
    cfg = get_config().model_copy(update={"address_as": "Mr. Dorman"})
    p = build_system_prompt(cfg, "", "", date(2026, 10, 30))
    assert '"Good morning, Mr. Dorman."' in p


async def test_a_bare_greeting_is_answered_without_claude():
    cfg = get_config().model_copy(update={"address_as": "Mr. Dorman"})
    svc = Services.create(cfg, None)
    try:
        brain = Brain(cfg, None, svc)
        brain._client = client = WindingDownClient(drain_s=0)
        reply = [d async for d in brain.ask("Good morning.")]
    finally:
        await svc.aclose()
    assert reply == ["Good morning, Mr. Dorman."]
    assert client.calls == []  # no query sent
