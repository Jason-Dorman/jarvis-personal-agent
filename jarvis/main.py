"""Start Jarvis.

    uv run python -m jarvis.main --text    # typed REPL + HUD (Chunk 1)
    uv run python -m jarvis.main           # full voice (Chunk 2, not built yet)

The HUD is at http://localhost:8765. Questions typed in the terminal and in the HUD
go to the same queue and are answered one at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import socket
import sys
import threading

import uvicorn

from hud.server import app, hud
from jarvis.agent import Brain
from jarvis.cards import ticker_items
from jarvis.runtime import Runtime
from jarvis.turns import TurnRunner
from tools.services import Services

log = logging.getLogger("jarvis.main")

TICKER_REFRESH_SECONDS = 300
QUIT_WORDS = {"quit", "exit", "/quit", "/exit"}


def read_stdin(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
    """Runs in a daemon thread so a blocked input() never holds up shutdown."""
    for line in sys.stdin:
        loop.call_soon_threadsafe(queue.put_nowait, line.strip())
    loop.call_soon_threadsafe(queue.put_nowait, "quit")


async def read_hud(queue: asyncio.Queue[str]) -> None:
    while True:
        msg = await hud.inbox.get()
        if msg.get("type") == "text" and (text := str(msg.get("text", "")).strip()):
            print(f"\nYou (HUD): {text}")
            await queue.put(text)
        elif msg.get("type") == "ptt" and msg.get("down"):
            print("\n(Push-to-talk needs voice mode, which arrives in Chunk 2. Type instead.)")


async def refresh_ticker(services: Services) -> None:
    while True:
        try:
            snapshot = await services.markets.get_watchlist_snapshot(services.watchlist())
            if items := ticker_items(snapshot):
                await hud.ticker(items)
        except Exception:
            log.exception("ticker refresh failed")
        await asyncio.sleep(TICKER_REFRESH_SECONDS)


async def answer(queue: asyncio.Queue[str], runner: TurnRunner, server: uvicorn.Server) -> None:
    while True:
        print("> ", end="", flush=True)
        question = await queue.get()
        if question.lower() in QUIT_WORDS:
            server.should_exit = True
            return
        if question:
            try:
                await runner.run(question)
            except Exception:
                log.exception("turn crashed")
                print("\n(That turn crashed. Details are in logs/jarvis.log.)")


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


async def run_text(port: int) -> None:
    if not port_is_free(port):
        print(f"Port {port} is busy. Is Jarvis (or hud/server.py) already running?")
        print("Close it, or pick another port with --port.")
        return
    async with Runtime() as rt:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        queue: asyncio.Queue[str] = asyncio.Queue()
        print(f"Jarvis (text mode). HUD at http://localhost:{port}")
        print("Starting the brain...", flush=True)
        async with Brain(rt.cfg, rt.memory, rt.services, hud=hud) as brain:
            print("Ready. Type a question, or 'quit'.")
            threading.Thread(
                target=read_stdin, args=(asyncio.get_running_loop(), queue), daemon=True
            ).start()
            helpers = [
                asyncio.create_task(read_hud(queue)),
                asyncio.create_task(refresh_ticker(rt.services)),
                asyncio.create_task(answer(queue, TurnRunner(brain, hud), server)),
            ]
            try:
                await server.serve()
            finally:
                for task in helpers:
                    task.cancel()
                for task in helpers:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        print("Bye.")


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvis")
    p.add_argument("--text", action="store_true", help="typed REPL + HUD, no audio")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    if not args.text:
        print("Voice mode arrives in Chunk 2. For now run: uv run python -m jarvis.main --text")
        sys.exit(2)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_text(args.port))


if __name__ == "__main__":
    main()
