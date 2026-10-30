"""One question → one answer, with the HUD kept in step.

The reply streams out of the brain, gets cut into sentences, and each sentence goes to
a `Voice`. With audio that's the `Speaker` (jarvis/voice_out.py), which sends `speech`
to the HUD when the sentence starts playing. In `--text` mode it's `Captions`, which
sends it the moment it's generated.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from typing import Any, Protocol, TextIO

from jarvis.speech import SentenceSplitter

FILLER = "Checking."
FILLER_AFTER_SECONDS = 0.8  # a tool call running this long, with nothing said yet


class TurnHud(Protocol):
    async def clear(self) -> None: ...
    async def heard(self, text: str, final: bool = True) -> None: ...
    async def state(self, value: str) -> None: ...
    async def speech(self, text: str) -> None: ...


class Voice(Protocol):
    async def say(self, sentence: str) -> None:
        """Queue one sentence. Returns without waiting for it to be spoken."""
        ...

    async def done(self) -> None:
        """Wait until everything queued has been spoken."""
        ...


class Answerer(Protocol):
    def ask(self, text: str) -> AsyncIterator[str]: ...
    def tool_running_for(self) -> float: ...


class Captions:
    """Text mode: no audio, so each sentence goes to the HUD as it's generated."""

    def __init__(self, hud: TurnHud) -> None:
        self.hud = hud

    async def say(self, sentence: str) -> None:
        await self.hud.state("speaking")
        await self.hud.speech(sentence)

    async def done(self) -> None:
        return None


class TurnRunner:
    """`filler` is said when a tool call runs long before anything else was said."""

    def __init__(
        self,
        brain: Answerer,
        hud: TurnHud,
        voice: Voice | None = None,
        out: TextIO = sys.stdout,
        filler: str | None = None,
    ) -> None:
        self.brain = brain
        self.hud = hud
        self.voice = voice or Captions(hud)
        self.out = out
        self.filler = filler

    async def run(self, question: str) -> str:
        await self.hud.clear()
        await self.hud.heard(question, final=True)
        await self.hud.state("thinking")
        splitter = SentenceSplitter()
        spoke = asyncio.Event()
        reply: list[str] = []
        self._write("Jarvis: ")

        async def say(sentence: str) -> None:
            spoke.set()
            await self.voice.say(sentence)

        filler = asyncio.create_task(self._filler(spoke)) if self.filler else None
        try:
            async for delta in self.brain.ask(question):
                reply.append(delta)
                self._write(delta)
                for sentence in splitter.feed(delta):
                    await say(sentence)
            for sentence in splitter.flush():
                await say(sentence)
            self._write("\n")
        finally:
            if filler:
                filler.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await filler
        await self.voice.done()
        await self.hud.state("idle")
        return "".join(reply)

    async def _filler(self, spoke: asyncio.Event) -> None:
        while not spoke.is_set():
            if self.brain.tool_running_for() >= FILLER_AFTER_SECONDS:
                await self.voice.say(self.filler)
                await self.voice.done()
                if not spoke.is_set():
                    await self.hud.state("thinking")  # back to working until the answer
                return
            await asyncio.sleep(0.1)

    def _write(self, text: Any) -> None:
        self.out.write(text)
        self.out.flush()
