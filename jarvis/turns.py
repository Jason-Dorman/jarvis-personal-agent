"""One question → one answer, with the HUD kept in step.

Text mode has no audio yet, so each sentence goes to the HUD as a `speech` event the
moment it's generated. Chunk 2 moves that call to when the sentence starts playing.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol, TextIO

from jarvis.agent import Brain
from jarvis.speech import SentenceSplitter


class TurnHud(Protocol):
    async def clear(self) -> None: ...
    async def heard(self, text: str, final: bool = True) -> None: ...
    async def state(self, value: str) -> None: ...
    async def speech(self, text: str) -> None: ...


class TurnRunner:
    def __init__(self, brain: Brain, hud: TurnHud, out: TextIO = sys.stdout) -> None:
        self.brain = brain
        self.hud = hud
        self.out = out

    async def run(self, question: str) -> str:
        await self.hud.clear()
        await self.hud.heard(question, final=True)
        await self.hud.state("thinking")
        splitter = SentenceSplitter()
        speaking = False
        reply: list[str] = []
        self._write("Jarvis: ")

        async def say(sentence: str) -> None:
            nonlocal speaking
            if not speaking:
                await self.hud.state("speaking")
                speaking = True
            await self.hud.speech(sentence)

        async for delta in self.brain.ask(question):
            reply.append(delta)
            self._write(delta)
            for sentence in splitter.feed(delta):
                await say(sentence)
        for sentence in splitter.flush():
            await say(sentence)
        self._write("\n")
        await self.hud.state("idle")
        return "".join(reply)

    def _write(self, text: Any) -> None:
        self.out.write(text)
        self.out.flush()
