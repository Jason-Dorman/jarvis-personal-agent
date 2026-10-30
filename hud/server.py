"""HUD bridge.

Serves the HUD page on localhost and pushes Jarvis events to it over a WebSocket.
The rest of Jarvis imports `hud` and calls its methods. Nothing else touches the screen.

Run from the repo root:
    uv run python hud/server.py --demo   # test turn on every question you type
    uv run python hud/server.py          # bridge only; your agent drives it

Then open http://localhost:8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

HERE = Path(__file__).parent


class Hud:
    """Broadcasts events to every open HUD and collects input coming back."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def emit(self, type: str, **data: Any) -> None:
        msg = json.dumps({"type": type, **data})
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # ---- one method per event type ----
    async def state(self, value: str) -> None:
        """idle | listening | thinking | speaking"""
        await self.emit("state", value=value)

    async def heard(self, text: str, final: bool = True) -> None:
        await self.emit("transcript", role="user", text=text, final=final)

    async def tool_call(self, tool: str, fn: str, args: dict | None = None) -> None:
        await self.emit("tool_call", tool=tool, fn=fn, args=args or {})

    async def tool_result(self, tool: str) -> None:
        """Use when a tool returns but produces no card."""
        await self.emit("tool_result", tool=tool)

    async def card(self, **card: Any) -> None:
        """A panel. Needs kind + title. See the spec for each kind's fields."""
        await self.emit("card", **card)

    async def speech(self, text: str) -> None:
        """Send each sentence the moment its audio starts playing."""
        await self.emit("speech", text=text)

    async def level(self, value: float) -> None:
        """Voice loudness 0..1, about 20 times a second while speaking."""
        await self.emit("level", value=round(max(0.0, min(1.0, value)), 3))

    async def ticker(self, items: list[dict]) -> None:
        await self.emit("ticker", items=items)

    async def clear(self) -> None:
        await self.emit("clear")


hud = Hud()


# ---------------------------------------------------------------------------
# Demo turn: proves the pipe works. Replace with the real agent.
# ---------------------------------------------------------------------------
async def _fake_levels(seconds: float) -> None:
    steps = max(1, int(seconds * 20))
    for i in range(steps):
        t = i / 20
        await hud.level(0.35 + 0.5 * abs(math.sin(t * 7.3) * math.sin(t * 3.1 + 1)))
        await asyncio.sleep(0.05)


async def demo_turn(question: str) -> None:
    await hud.clear()
    await hud.heard(question)
    await hud.state("thinking")
    await hud.tool_call("pegs", "get_peg", {"stablecoin": "USDC"})
    await asyncio.sleep(0.9)
    series = [
        round(0.9998 + 0.0002 * math.sin(i / 3) + random.uniform(-5e-5, 5e-5), 5) for i in range(36)
    ]
    await hud.card(
        id="usdc-test",
        tool="pegs",
        kind="metric",
        title="USDC peg (test data)",
        keys=["usdc"],
        value=series[-1],
        decimals=4,
        unit="USD",
        delta=1.2,
        delta_label="supply, 7 days",
        spark=series,
        source="Bridge test",
    )
    await hud.state("speaking")
    for line in [
        "This is a test turn from the localhost bridge.",
        "The USDC panel you see is test data, not a live price.",
    ]:
        await hud.speech(line)
        await _fake_levels(len(line) * 0.058)
    await hud.state("idle")


async def demo_loop() -> None:
    while True:
        msg = await hud.inbox.get()
        if msg.get("type") == "text":
            asyncio.create_task(demo_turn(msg.get("text", "")))
        elif msg.get("type") == "ptt":
            if msg.get("down"):
                await hud.state("listening")
            else:
                asyncio.create_task(demo_turn("Push-to-talk test"))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
DEMO = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(demo_loop()) if DEMO else None
    yield
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    hud.clients.add(ws)
    try:
        while True:
            await hud.inbox.put(await ws.receive_json())
    except WebSocketDisconnect:
        pass
    finally:
        hud.clients.discard(ws)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--demo", action="store_true", help="answer every question with a test turn")
    a = p.parse_args()
    DEMO = a.demo
    uvicorn.run(app, host="127.0.0.1", port=a.port)
