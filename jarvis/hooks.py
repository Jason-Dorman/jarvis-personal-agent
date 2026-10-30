"""Agent SDK pre/post tool hooks: HUD events, the tool call log, and panel hints.

The tools never know the HUD exists (DECISIONS.md). These hooks see every call:
`tool_call` goes out before it runs, cards (or `tool_result`) after, and the call is
written to the `tool_calls` table.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

from claude_agent_sdk import HookContext, HookMatcher

from jarvis.cards import cards_for, panel_hint
from jarvis.memory import Memory
from tools.registry import split_mcp_name

log = logging.getLogger("jarvis.hooks")


class HudSink(Protocol):
    async def tool_call(self, tool: str, fn: str, args: dict | None = None) -> None: ...
    async def tool_result(self, tool: str) -> None: ...
    async def card(self, **card: Any) -> None: ...


def parse_tool_response(response: Any) -> Any:
    """Recover our tool's JSON result from whatever shape the CLI hands the hook."""
    if isinstance(response, dict) and "content" in response:
        response = response["content"]
    if isinstance(response, list):
        texts = [
            b.get("text", "") for b in response if isinstance(b, dict) and b.get("type") == "text"
        ]
        response = "".join(texts)
    if isinstance(response, str):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"raw": response}
    return response


class ToolHooks:
    """One per session. `called` lists (tool, fn) for the current turn."""

    def __init__(self, memory: Memory | None, hud: HudSink | None = None) -> None:
        self.memory = memory
        self.hud = hud
        self.called: list[tuple[str, str]] = []
        self._started: dict[str, float] = {}

    def start_turn(self) -> None:
        self.called = []
        self._started = {}

    def matchers(self) -> dict[str, list[HookMatcher]]:
        return {
            "PreToolUse": [HookMatcher(hooks=[self.pre])],
            "PostToolUse": [HookMatcher(hooks=[self.post])],
            "PostToolUseFailure": [HookMatcher(hooks=[self.failure])],
        }

    async def pre(self, data: Any, tool_use_id: str | None, ctx: HookContext) -> dict[str, Any]:
        name = split_mcp_name(data.get("tool_name", ""))
        if not name:
            return {}
        self.called.append(name)
        self._started[data.get("tool_use_id") or tool_use_id or ""] = time.monotonic()
        await self._hud("tool_call", name[0], name[1], data.get("tool_input") or {})
        return {}

    async def post(self, data: Any, tool_use_id: str | None, ctx: HookContext) -> dict[str, Any]:
        name = split_mcp_name(data.get("tool_name", ""))
        if not name:
            return {}
        tool, fn = name
        result = parse_tool_response(data.get("tool_response"))
        err = result.get("error") if isinstance(result, dict) else None
        self._log(data, tool_use_id, tool, fn, result, err)
        cards = cards_for(tool, fn, result)
        if self.hud:
            await self._show(tool, cards)
        hint = panel_hint(cards) if self.hud else ""
        if not hint:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": hint}}

    async def failure(self, data: Any, tool_use_id: str | None, ctx: HookContext) -> dict[str, Any]:
        name = split_mcp_name(data.get("tool_name", ""))
        if not name:
            return {}
        self._log(data, tool_use_id, name[0], name[1], None, data.get("error") or "tool failed")
        if self.hud:
            await self._safe(self.hud.tool_result(name[0]))
        return {}

    # ---- helpers ----
    def _log(
        self, data: Any, tool_use_id: str | None, tool: str, fn: str, result: Any, err: Any
    ) -> None:
        if not self.memory:
            return
        duration = data.get("duration_ms")
        if duration is None:
            started = self._started.pop(data.get("tool_use_id") or tool_use_id or "", None)
            duration = int((time.monotonic() - started) * 1000) if started else None
        try:
            self.memory.log_tool_call(
                tool, fn, data.get("tool_input") or {}, result, error=err, duration_ms=duration
            )
        except Exception:
            log.exception("could not log tool call %s.%s", tool, fn)

    async def _show(self, tool: str, cards: list[dict[str, Any]]) -> None:
        if not cards:
            await self._safe(self.hud.tool_result(tool))
            return
        for card in cards:
            await self._safe(self.hud.card(**card))

    async def _hud(self, method: str, *args: Any) -> None:
        if self.hud:
            await self._safe(getattr(self.hud, method)(*args))

    @staticmethod
    async def _safe(coro: Any) -> None:
        try:
            await coro
        except Exception:
            log.exception("HUD event failed")
