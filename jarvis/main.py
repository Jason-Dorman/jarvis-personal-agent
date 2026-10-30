"""Start Jarvis.

    uv run python -m jarvis.main           # full voice: wake word, push-to-talk, speech
    uv run python -m jarvis.main --text    # typed REPL + HUD, no audio

The HUD is at http://localhost:8765. Questions typed in the terminal and in the HUD go
to the same queue as voice and are answered one at a time (jarvis/conversation.py).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import socket
import sys
import threading
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import uvicorn

from hud.server import app, hud
from jarvis.agent import Brain
from jarvis.cards import ticker_items
from jarvis.conversation import Inputs, VoiceLoop, text_loop
from jarvis.runtime import Runtime
from jarvis.turns import FILLER, TurnRunner
from tools.services import Services

log = logging.getLogger("jarvis.main")

TICKER_REFRESH_SECONDS = 300

# A mode loads what it needs into the exit stack and returns the loop to run.
Mode = Callable[[Runtime, Inputs, contextlib.AsyncExitStack], Awaitable[Coroutine[Any, Any, None]]]


def read_stdin(loop: asyncio.AbstractEventLoop, inputs: Inputs) -> None:
    """Runs in a daemon thread so a blocked input() never holds up shutdown."""
    for line in sys.stdin:
        loop.call_soon_threadsafe(inputs.put, "text", line.strip())
    loop.call_soon_threadsafe(inputs.put, "quit")


async def read_hud(inputs: Inputs) -> None:
    while True:
        msg = await hud.inbox.get()
        if msg.get("type") == "text" and (text := str(msg.get("text", "")).strip()):
            print(f"\nYou (HUD): {text}")
            inputs.put("text", text)
        elif msg.get("type") == "ptt":
            inputs.put("ptt", bool(msg.get("down")))


async def refresh_ticker(services: Services) -> None:
    while True:
        try:
            snapshot = await services.markets.get_watchlist_snapshot(services.watchlist())
            if items := ticker_items(snapshot):
                await hud.ticker(items)
        except Exception:
            log.exception("ticker refresh failed")
        await asyncio.sleep(TICKER_REFRESH_SECONDS)


async def text_mode(
    rt: Runtime, inputs: Inputs, stack: contextlib.AsyncExitStack
) -> Coroutine[Any, Any, None]:
    print("Jarvis (text mode).")
    brain = await _start_brain(rt, stack)
    print("Ready. Type a question, or 'quit'.")
    return text_loop(inputs, TurnRunner(brain, hud))


async def voice_mode(
    rt: Runtime, inputs: Inputs, stack: contextlib.AsyncExitStack
) -> Coroutine[Any, Any, None]:
    # Imported here so --text never loads the audio stack. This still runs on the main
    # thread, which matters: native DLLs must not load from a worker (README).
    from jarvis import audio, models
    from jarvis.config import LOGS_DIR
    from jarvis.voice_in import Ears, Mic, StreamingVad, Transcriber, WakeWord
    from jarvis.voice_out import Speaker

    cfg = rt.cfg
    print("Jarvis (voice mode). Loading speech models...", flush=True)
    speaker = Speaker.load(str(models.piper_voice(cfg.tts.voice)), hud, cfg.audio.output_device)
    wake = WakeWord(models.wake_files(cfg.wake_word.model), cfg.wake_word.sensitivity)
    stt = Transcriber.load(cfg.stt)
    mic_device = audio.find_device("input", cfg.audio.input_device)
    print(f"  Speech-to-text: whisper {cfg.stt.model} on {stt.device.upper()}")
    print(f"  Mic: {audio.device_name(mic_device, 'input')}")
    print(f"  Speakers: {audio.device_name(speaker.device, 'output')}")

    brain = await _start_brain(rt, stack)
    await stack.enter_async_context(speaker)
    mic = await stack.enter_async_context(Mic(mic_device))
    ears = Ears(mic, StreamingVad(), wake, stt, LOGS_DIR / "audio" if cfg.debug else None)
    runner = TurnRunner(brain, hud, speaker, filler=FILLER)
    print('Ready. Say "Hey Jarvis", hold Space in the HUD, or type. "quit" to stop.')
    return VoiceLoop(
        ears,
        speaker,
        runner,
        hud,
        inputs,
        silence=cfg.vad.silence_seconds,
        vad_threshold=cfg.vad.threshold,
        followup_seconds=cfg.followup_window_seconds,
    ).run()


async def _start_brain(rt: Runtime, stack: contextlib.AsyncExitStack) -> Brain:
    print("Starting the brain...", flush=True)
    return await stack.enter_async_context(Brain(rt.cfg, rt.memory, rt.services, hud=hud))


async def _until_done(loop: Coroutine[Any, Any, None], server: uvicorn.Server) -> None:
    try:
        await loop
    finally:
        server.should_exit = True


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


async def run(port: int, mode: Mode) -> None:
    if not port_is_free(port):
        print(f"Port {port} is busy. Is Jarvis (or hud/server.py) already running?")
        print("Close it, or pick another port with --port.")
        return
    async with Runtime() as rt, contextlib.AsyncExitStack() as stack:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        inputs = Inputs()
        loop = await mode(rt, inputs, stack)
        print(f"HUD at http://localhost:{port}")
        threading.Thread(
            target=read_stdin, args=(asyncio.get_running_loop(), inputs), daemon=True
        ).start()
        helpers = [
            asyncio.create_task(read_hud(inputs)),
            asyncio.create_task(refresh_ticker(rt.services)),
            asyncio.create_task(_until_done(loop, server)),
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
    # Git Bash (mintty) gives Python a pipe, not a console, so prints would sit in a
    # buffer and "Ready." would never show. Line buffering sends each line right away.
    sys.stdout.reconfigure(line_buffering=True)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args.port, text_mode if args.text else voice_mode))


if __name__ == "__main__":
    main()
