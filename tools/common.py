"""Helpers shared by every tool: error results, the MCP wrapper, HTTP, and a small cache.

Tool rule (spec 9, CLAUDE.md): a tool never raises into the model. Data functions return
plain dicts. On failure they return `{"error": "..."}` so Jarvis can say "unavailable".
"""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger("jarvis.tools")

USER_AGENT = "Mozilla/5.0 (compatible; jarvis/0.1; personal assistant)"


def error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, **extra}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=6.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def describe_http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "rate limited by the data source, try again in a minute"
        return f"data source returned HTTP {code}"
    if isinstance(exc, httpx.TimeoutException):
        return "data source timed out"
    if isinstance(exc, httpx.HTTPError):
        return "could not reach the data source"
    return f"unexpected error: {type(exc).__name__}"


def mcp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a data result as MCP tool output. The model reads the JSON text."""
    return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}


Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def safe_tool(fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> Handler:
    """Turn a data call into an MCP handler that can't raise."""

    @functools.wraps(fn)
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await fn(args)
        except Exception as exc:  # never raise into the model
            log.exception("tool %s failed", fn.__name__)
            result = error(describe_http_error(exc))
        return mcp_result(result)

    return handler


class TtlCache:
    """Tiny in-memory cache so repeated questions don't hammer free APIs."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = ttl_seconds
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._items.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        self._items[key] = (time.monotonic(), value)


def round_price(price: float) -> float:
    """Keep enough precision to be useful without flooding the model with digits."""
    if abs(price) >= 1000:
        return round(price, 2)
    if abs(price) >= 1:
        return round(price, 4)
    return round(price, 6)


def pct_change(new: float, old: float) -> float | None:
    if not old:
        return None
    return round((new - old) / old * 100, 2)
