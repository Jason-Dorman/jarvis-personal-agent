"""The conversation loops. One question is answered at a time.

Voice mode owns the states (spec 4, 15.3):

    idle ──wake word / hold Space──► listening ──► thinking ──► speaking
     ▲                                                              │
     └──── nobody spoke for 8 s ◄── listening (follow-up, no wake) ◄┘

Typed questions (HUD or terminal) and push-to-talk arrive on `Inputs` and feed the
same turns. Text typed while Jarvis is busy waits its turn. Push-to-talk presses made
while Jarvis is busy are dropped (barge-in is Chunk 3).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Coroutine
from typing import Any, Literal, Protocol

from jarvis.turns import TurnRunner
from jarvis.voice_in import Ears, Listen, clean_transcript

log = logging.getLogger("jarvis.conversation")

QUIT_WORDS = {"quit", "exit", "/quit", "/exit"}
WAKE_START_TIMEOUT = 4.0  # after "Hey Jarvis", how long to wait for the question
ECHO_TAIL_SECONDS = 0.15  # let the speakers finish before the follow-up mic opens

Kind = Literal["text", "ptt", "quit"]
Event = tuple[Kind, Any]


class Quit(Exception):
    pass


class Inputs:
    """Typed questions and push-to-talk, from the HUD and the terminal."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[Event] = asyncio.Queue()
        self._held: deque[Event] = deque()

    def put(self, kind: Kind, value: Any = None) -> None:
        self._q.put_nowait((kind, value))

    async def get(self) -> Event:
        if self._held:
            return self._held.popleft()
        return await self._q.get()

    def unget(self, event: Event) -> None:
        self._held.appendleft(event)

    def drop_push_to_talk(self) -> None:
        """Forget presses made while Jarvis was busy. Keep everything else."""
        keep = [e for e in self._held if e[0] != "ptt"]
        while not self._q.empty():
            if (e := self._q.get_nowait())[0] != "ptt":
                keep.append(e)
        self._held = deque(keep)


async def run_turn(runner: TurnRunner, question: str) -> None:
    try:
        await runner.run(question)
    except Exception:
        log.exception("turn crashed")
        print("\n(That turn crashed. Details are in logs/jarvis.log.)")


def _is_quit(event: Event) -> bool:
    kind, value = event
    return kind == "quit" or (kind == "text" and str(value).lower() in QUIT_WORDS)


async def text_loop(inputs: Inputs, runner: TurnRunner) -> None:
    """`--text`: answer typed questions. Returns on quit."""
    while True:
        print("> ", end="", flush=True)
        event = await inputs.get()
        if _is_quit(event):
            return
        kind, value = event
        if kind == "ptt":
            if value:
                print("\n(Push-to-talk needs voice mode: uv run python -m jarvis.main)")
        elif value:
            await run_turn(runner, value)


class Chime(Protocol):
    async def chime(self) -> None: ...


class ListenHud(Protocol):
    async def state(self, value: str) -> None: ...
    async def heard(self, text: str, final: bool = True) -> None: ...


class VoiceLoop:
    def __init__(
        self,
        ears: Ears,
        mouth: Chime,
        runner: TurnRunner,
        hud: ListenHud,
        inputs: Inputs,
        *,
        silence: float,
        vad_threshold: float,
        followup_seconds: float,
    ) -> None:
        self.ears = ears
        self.mouth = mouth
        self.runner = runner
        self.hud = hud
        self.inputs = inputs
        self.after_wake = Listen(WAKE_START_TIMEOUT, silence, vad_threshold)
        self.follow_up = Listen(followup_seconds, silence, vad_threshold)

    async def run(self) -> None:
        """Returns on quit."""
        with contextlib.suppress(Quit):
            while True:
                question = await self._idle()
                while question:
                    await run_turn(self.runner, question)
                    self.inputs.drop_push_to_talk()
                    question = await self._follow_up()

    # ---- states ----
    async def _idle(self) -> str:
        """Wait for the wake word, a typed question, or push-to-talk."""
        while True:
            await self.hud.state("idle")
            self.ears.reset()
            _, event = await self._race(self.ears.wait_for_wake())
            if event:
                question = await self._handle(event)
            else:
                await self.mouth.chime()
                await self.hud.state("listening")
                question = await self._hear(self.after_wake, after_wake=True)
            if question:
                return question

    async def _follow_up(self) -> str | None:
        """Keep listening for a while after answering, no wake word needed."""
        if self.follow_up.start_timeout <= 0:
            return None
        await asyncio.sleep(ECHO_TAIL_SECONDS)
        self.ears.reset()  # drop Jarvis's own voice
        await self.hud.state("listening")
        return await self._hear(self.follow_up)

    async def _hear(self, listen: Listen, after_wake: bool = False) -> str | None:
        """Record one question by voice, unless typing or push-to-talk cuts in."""
        pcm, event = await self._race(self.ears.capture(listen, self._partial, after_wake))
        if event:
            return await self._handle(event)
        if pcm is None:
            return None
        return await self._transcribe(pcm)

    async def _push_to_talk(self) -> str | None:
        self.ears.reset()
        await self.mouth.chime()
        await self.hud.state("listening")
        released = asyncio.Event()
        watcher = asyncio.create_task(self._wait_for_release(released))
        try:
            pcm = await self.ears.record(released, self._partial)
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        return await self._transcribe(pcm)

    # ---- helpers ----
    async def _handle(self, event: Event) -> str | None:
        if _is_quit(event):
            raise Quit
        kind, value = event
        if kind == "text":
            return value or None
        if kind == "ptt" and value:
            return await self._push_to_talk()
        return None  # a stray release

    async def _wait_for_release(self, released: asyncio.Event) -> None:
        others: list[Event] = []
        try:
            while True:
                event = await self.inputs.get()
                if event[0] == "ptt" and not event[1]:
                    return
                if _is_quit(event):
                    others.append(event)
                    return
                if event[0] != "ptt":
                    others.append(event)
        finally:
            released.set()
            for event in reversed(others):
                self.inputs.unget(event)

    async def _transcribe(self, pcm: Any) -> str | None:
        if len(pcm) == 0:
            return None
        await self.hud.state("thinking")
        text = clean_transcript(await self.ears.transcribe(pcm))
        if not text:
            print("\n(Didn't catch that.)")
            return None
        print(f"\nYou (voice): {text}")
        return text

    async def _partial(self, text: str) -> None:
        await self.hud.heard(text, final=False)

    async def _race(self, work: Coroutine[Any, Any, Any]) -> tuple[Any, Event | None]:
        """Run `work` until it finishes or an input arrives, whichever is first."""
        work_task = asyncio.create_task(work)
        input_task = asyncio.create_task(self.inputs.get())
        done, _ = await asyncio.wait({work_task, input_task}, return_when=asyncio.FIRST_COMPLETED)
        if work_task in done:
            await _cancel(input_task)
            if input_task.done() and not input_task.cancelled():
                self.inputs.unget(input_task.result())  # both finished: keep the input
            return work_task.result(), None
        await _cancel(work_task)
        return None, input_task.result()


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
