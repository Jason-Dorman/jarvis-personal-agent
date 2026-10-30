"""The brain: one Claude Agent SDK session with Jarvis's tools and voice rules (spec 6.4).

Safety (spec 9): every built-in Agent SDK tool is off (`tools=[]`), and only Jarvis's
MCP tools are allowed. Anything else is denied without asking (`dontAsk`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

from jarvis.brief import save_brief
from jarvis.config import ROOT, Config, model_id
from jarvis.hooks import HudSink, ToolHooks
from jarvis.memory import Memory
from tools import registry
from tools.services import Services

log = logging.getLogger("jarvis.agent")

MAX_AGENT_TURNS = 12
SORRY = "Sorry, something went wrong on my end. Try that again."

_DEEP = re.compile(r"\b(think hard|deep dive)\b", re.IGNORECASE)


def wants_deep(text: str) -> bool:
    """'think hard' or 'deep dive' switches this turn to the deep model."""
    return bool(_DEEP.search(text))


SYSTEM_PROMPT = """\
You are Jarvis, a voice assistant for markets, crypto, and fintech. Your replies are spoken \
out loud and shown as captions, so write the way a person talks.

How to answer
- Use {max_sentences} short sentences at most, and one or two is usually best, unless the \
user asks for more. Lead with the answer. Give the key numbers, not every number.
- Say numbers the way people speak them: "ninety-nine point nine eight cents", "about \
seventy-seven thousand dollars", "down four point six percent". Round for the ear.
- Plain sentences only. No lists, tables, markdown, emoji, or bare symbols.
- Every number you say must come from a tool result in this conversation. If no tool has \
it, say you don't have that data. If a tool returns an error, say that data is unavailable \
right now. Never estimate or recall prices from memory.
- You are read-only. You can't trade, move money, or send messages. Say so if asked.
- When you need data, call the tool straight away without announcing it.

Panels on screen
Tool results also appear as panels on the user's screen. After a tool runs you'll be told \
which panels are showing and their key words. When you talk about a panel, say one of its \
key words so it lights up.

Tools
- Prices: markets.get_price for one symbol, get_watchlist_snapshot for the whole \
watchlist, get_movers for "what moved".
- Stablecoins: pegs.get_peg for one coin, get_supply_change for supply over N days, \
list_peg_alerts for "are the pegs okay".
- News: news.get_headlines, and get_article_summary for one link.
- Notes: when the user says "note that", "remember", or "save this", call \
notes.append_note. Investment theses go to "theses" with a short heading. Other thoughts \
go to "inbox". Then confirm in a few words, like "Saved to your theses."

Morning brief
Only when the user asks for the morning brief (or "the brief", "run the brief"). Call \
brief.get_brief_data once, then speak the brief in this order:
1. One-line market mood, from the watchlist moves.
2. Watchlist: anything that moved more than the threshold. If nothing did, one short line.
3. Stablecoin pegs: any stress, or "all pegs normal".
4. The top three fintech or digital-asset headlines.
5. Anything in the data that touches one of the user's theses.
Aim for 60 to 90 seconds spoken, about 150 to 220 words, in short spoken paragraphs. The \
brief is the only answer allowed past the sentence limit. Skip quiet sections in one line.

Today is {today}.

The user's watchlist (notes/watchlist.md):
{watchlist}

The user's theses (notes/theses.md):
{theses}
"""


def build_system_prompt(cfg: Config, watchlist: str, theses: str, today: date) -> str:
    return SYSTEM_PROMPT.format(
        max_sentences=cfg.brain.max_spoken_sentences,
        today=today.strftime("%A, %B %d, %Y"),
        watchlist=watchlist.strip() or "(empty)",
        theses=theses.strip() or "(none yet)",
    )


class Brain:
    """`async with Brain(...) as brain:` then `async for text in brain.ask(q)`."""

    def __init__(
        self,
        cfg: Config,
        memory: Memory | None,
        services: Services,
        hud: HudSink | None = None,
    ) -> None:
        self.cfg = cfg
        self.memory = memory
        self.services = services
        self.hooks = ToolHooks(memory, hud)
        self.session_id = str(uuid4())
        self.default_model = model_id(cfg.brain.default_model)
        self.deep_model = model_id(cfg.brain.deep_model)
        self.model = self.default_model
        self.last_brief_path: Path | None = None
        self._client: ClaudeSDKClient | None = None

    def options(self) -> ClaudeAgentOptions:
        servers, allowed = registry.build(self.services)
        prompt = build_system_prompt(
            self.cfg, self.services.watchlist_text(), self.services.theses_text(), date.today()
        )
        return ClaudeAgentOptions(
            tools=[],  # no built-in tools at all: no Bash, files, web, or subagents
            allowed_tools=allowed,
            mcp_servers=servers,
            strict_mcp_config=True,
            permission_mode="dontAsk",
            setting_sources=[],  # ignore any CLAUDE.md or user settings on this machine
            skills=[],
            verbatim_prompts=True,
            system_prompt=prompt,
            model=self.model,
            effort=self.cfg.brain.effort,
            include_partial_messages=True,
            hooks=self.hooks.matchers(),
            max_turns=MAX_AGENT_TURNS,
            cwd=str(ROOT),
            env={"CLAUDE_AGENT_SDK_CLIENT_APP": "jarvis/0.1"},
            stderr=lambda line: log.debug("cli: %s", line.rstrip()),
        )

    async def __aenter__(self) -> Brain:
        self._client = ClaudeSDKClient(self.options())
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def ask(self, text: str) -> AsyncIterator[str]:
        """Stream the reply text as it's generated. Never raises for API trouble."""
        assert self._client, "use `async with Brain(...)` first"
        self.hooks.start_turn()
        self._log("user", text)
        parts: list[str] = []
        try:
            await self._pick_model(text)
            async for delta in self._stream(text):
                parts.append(delta)
                yield delta
        except Exception:
            log.exception("turn failed")
            sep = " " if parts else ""
            parts.append(sep + SORRY)
            yield sep + SORRY
        reply = "".join(parts).strip()
        self._log("assistant", reply, self.model)
        self._maybe_save_brief(reply)

    def tool_running_for(self) -> float:
        """Seconds the current turn's slowest unfinished tool call has been running."""
        return self.hooks.running_for()

    async def _pick_model(self, text: str) -> None:
        want = self.deep_model if wants_deep(text) else self.default_model
        if want != self.model:
            await self._client.set_model(want)
            self.model = want

    async def _stream(self, text: str) -> AsyncIterator[str]:
        """Text deltas from the main thread of the turn, with a space between messages."""
        await self._client.query(text)
        emitted = ""
        streamed_this_message = False
        async for msg in self._client.receive_response():
            if isinstance(msg, StreamEvent) and msg.parent_tool_use_id is None:
                ev = msg.event
                if ev.get("type") == "message_start":
                    streamed_this_message = False
                elif ev.get("type") == "content_block_delta":
                    delta = ev.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        chunk = delta["text"]
                        if not streamed_this_message and emitted and not emitted[-1].isspace():
                            chunk = " " + chunk
                        streamed_this_message = True
                        emitted += chunk
                        yield chunk
            elif isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                if msg.error:
                    log.warning("assistant error: %s", msg.error)
                full = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                if full and not streamed_this_message:  # streaming didn't deliver it
                    chunk = (" " if emitted and not emitted[-1].isspace() else "") + full
                    emitted += chunk
                    yield chunk
                streamed_this_message = False
            elif isinstance(msg, ResultMessage):
                log.info(
                    "turn done in %sms (api %sms), %s agent turns, cost $%s",
                    msg.duration_ms,
                    msg.duration_api_ms,
                    msg.num_turns,
                    msg.total_cost_usd,
                )
                if msg.is_error and not emitted.strip():
                    log.warning("turn ended with error: %s %s", msg.subtype, msg.errors)
                    yield SORRY

    def _log(self, role: str, text: str, model: str | None = None) -> None:
        if not self.memory or not text:
            return
        try:
            self.memory.log_turn(self.session_id, role, text, model)
        except Exception:
            log.exception("could not log %s turn", role)

    def _maybe_save_brief(self, reply: str) -> None:
        if ("brief", "get_brief_data") not in self.hooks.called or not reply:
            return
        self.last_brief_path = save_brief(reply)
        log.info("brief saved to %s", self.last_brief_path)
