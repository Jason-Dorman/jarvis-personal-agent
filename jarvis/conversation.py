"""The conversation loops. One question is answered at a time.

Voice mode owns the states (spec 4, 15.3):

    idle ──wake word / hold Space──► listening ──► thinking ──► speaking
     ▲                                                              │
     └──── nobody spoke for 8 s ◄── listening (follow-up, no wake) ◄┘

Typed questions (HUD or terminal) and push-to-talk arrive on `Inputs` and feed the
same turns. Text typed while Jarvis is busy waits its turn.

Barge-in (spec 6.6): while a turn runs, the ears keep listening. The wake word, a
push-to-talk press, or (with `barge_in: voice`) talking over Jarvis stops playback,
cancels the reply, and starts the next question straight away.

    thinking / speaking ──"Hey Jarvis"──► chime, listening
                        ──push-to-talk──► listening while held
                        ──you talk──────► listening (keeps what you already said)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Literal, Protocol

from jarvis.timing import TurnClock
from jarvis.turns import TurnRunner
from jarvis.voice_in import BargeIn, Ears, Listen, clean_transcript, sounds_like_echo

log = logging.getLogger("jarvis.conversation")

QUIT_WORDS = {"quit", "exit", "/quit", "/exit"}
WAKE_START_TIMEOUT = 4.0  # after "Hey Jarvis", how long to wait for the question
ECHO_TAIL_SECONDS = 0.15  # let the speakers finish before the follow-up mic opens
ECHO_WARNING = (
    "\n(Jarvis heard its own voice, so talking over it is off until restart."
    ' Use headphones with barge_in: voice, or say "Hey Jarvis" to interrupt.)'
)

Kind = Literal["text", "ptt", "quit"]
Event = tuple[Kind, Any]
BargeInMode = Literal["wake_word", "voice", "off"]


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


class Mouth(Protocol):
    async def chime(self) -> None: ...
    def stop(self) -> None: ...
    def recent_speech(self) -> str: ...

    @property
    def playing(self) -> bool: ...


class ListenHud(Protocol):
    async def state(self, value: str) -> None: ...
    async def heard(self, text: str, final: bool = True) -> None: ...


class VoiceLoop:
    def __init__(
        self,
        ears: Ears,
        mouth: Mouth,
        runner: TurnRunner,
        hud: ListenHud,
        inputs: Inputs,
        *,
        silence: float,
        vad_threshold: float,
        followup_seconds: float,
        barge_in: BargeInMode = "wake_word",
        clock: TurnClock | None = None,
        on_wake: Callable[[], None] | None = None,
    ) -> None:
        self.ears = ears
        self.mouth = mouth
        self.runner = runner
        self.hud = hud
        self.inputs = inputs
        self.barge_in = barge_in
        self.vad_threshold = vad_threshold
        self.clock = clock or TurnClock()
        self.on_wake = on_wake or (lambda: None)  # e.g. warm the data caches
        self.after_wake = Listen(WAKE_START_TIMEOUT, silence, vad_threshold)
        self.follow_up = Listen(followup_seconds, silence, vad_threshold)

    async def run(self) -> None:
        """Returns on quit."""
        with contextlib.suppress(Quit):
            while True:
                question = await self._idle()
                while question:
                    question = await self._answer(question)

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
                question = await self._woke()
            if question:
                return question

    async def _answer(self, question: str) -> str | None:
        """Answer, then return the next question: a barge-in, a follow-up, or None."""
        cut_in = await self._speak_unless_interrupted(question)
        if cut_in:
            return await self._after_barge_in(cut_in)
        self.inputs.drop_push_to_talk()
        return await self._follow_up()

    async def _speak_unless_interrupted(self, question: str) -> BargeIn | None:
        """Run the turn while listening for barge-in (spec 6.6). Returns how it was cut."""
        if self.barge_in == "off":
            await run_turn(self.runner, question)
            return None
        turn = asyncio.create_task(run_turn(self.runner, question))
        watch = asyncio.create_task(self._watch_for_barge_in())
        done, _ = await asyncio.wait({turn, watch}, return_when=asyncio.FIRST_COMPLETED)
        if watch not in done:
            await _cancel(watch)
            return None
        self.mouth.stop()
        if not turn.done():
            await _cancel(turn)
            await self.runner.interrupt()
        return watch.result()

    async def _after_barge_in(self, cut_in: BargeIn) -> str | None:
        print("\n(Interrupted.)")
        if cut_in.by in ("wake", "ptt"):
            await asyncio.sleep(ECHO_TAIL_SECONDS)
            self.ears.reset()  # the last moment was mostly Jarvis's own voice
        if cut_in.by == "wake":
            return await self._woke()
        if cut_in.by == "ptt":
            return await self._push_to_talk()
        await self.hud.state("listening")  # voice: you're already talking, so no chime
        question = await self._hear(self.follow_up, lead=cut_in.lead)
        if question and sounds_like_echo(question, self.mouth.recent_speech()):
            log.warning("voice barge-in heard Jarvis itself (%r); switching to wake word", question)
            print(ECHO_WARNING)
            self.barge_in = "wake_word"
            return None
        return question

    async def _follow_up(self) -> str | None:
        """Keep listening for a while after answering, no wake word needed."""
        if self.follow_up.start_timeout <= 0:
            return None
        await asyncio.sleep(ECHO_TAIL_SECONDS)
        self.ears.reset()  # drop Jarvis's own voice
        await self.hud.state("listening")
        return await self._hear(self.follow_up)

    async def _woke(self) -> str | None:
        """The wake word fired: chime, then record the question."""
        self.clock.mark("wake")
        self.on_wake()
        await self.hud.state("listening")
        self.clock.mark("chime")
        await self.mouth.chime()
        return await self._hear(self.after_wake, after_wake=True)

    async def _hear(
        self, listen: Listen, after_wake: bool = False, lead: Sequence[Any] = ()
    ) -> str | None:
        """Record one question by voice, unless typing or push-to-talk cuts in."""
        capture = self.ears.capture(listen, self._partial, after_wake, lead)
        pcm, event = await self._race(capture)
        if event:
            return await self._handle(event)
        if pcm is None:
            self.clock.discard()
            return None
        self.clock.mark("speech_end", self.ears.last_speech_at)
        self.clock.mark("endpointed")
        return await self._transcribe(pcm)

    async def _push_to_talk(self) -> str | None:
        self.clock.discard()
        self.clock.mark("ptt")
        self.on_wake()
        self.ears.reset()
        await self.hud.state("listening")
        await self.mouth.chime()
        released = asyncio.Event()
        watcher = asyncio.create_task(self._wait_for_release(released))
        try:
            pcm = await self.ears.record(released, self._partial)
        finally:
            await _cancel(watcher)
        self.clock.mark("speech_end")
        self.clock.mark("endpointed")
        return await self._transcribe(pcm)

    async def _watch_for_barge_in(self) -> BargeIn:
        """While a turn runs. Push-to-talk cuts in too. Typed questions wait their turn."""
        held: list[Event] = []
        try:
            while True:
                heard = self.ears.watch_for_barge_in(
                    self.barge_in == "voice", self.vad_threshold, lambda: self.mouth.playing
                )
                cut_in, event = await self._race(heard)
                if event is None:
                    return cut_in
                if event[0] == "ptt" and event[1]:
                    return BargeIn("ptt")
                if event[0] != "ptt":
                    held.append(event)
        finally:
            for event in reversed(held):
                self.inputs.unget(event)

    # ---- helpers ----
    async def _handle(self, event: Event) -> str | None:
        if _is_quit(event):
            raise Quit
        kind, value = event
        if kind == "text":
            self.clock.discard()  # a typed question times itself from when it's asked
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
            self.clock.discard()
            return None
        await self.hud.state("thinking")
        text = clean_transcript(await self.ears.transcribe(pcm))
        self.clock.mark("transcribed")
        if not text:
            self.clock.discard()
            print("\n(Didn't catch that.)")
            return None
        print(f"\nYou (voice): {text}")
        return text

    async def _partial(self, text: str) -> None:
        await self.hud.heard(text, final=False)

    async def _race(self, work: Coroutine[Any, Any, Any]) -> tuple[Any, Event | None]:
        """Run `work` until it finishes or an input arrives, whichever is first.

        If the race itself is cancelled (a turn ending stops its barge-in watcher), both
        sides are stopped too. Left running, they'd steal mic frames and inputs."""
        work_task = asyncio.create_task(work)
        input_task = asyncio.create_task(self.inputs.get())
        try:
            done, _ = await asyncio.wait(
                {work_task, input_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await _cancel(work_task)
            await self._cancel_input(input_task)
            raise
        if work_task in done:
            await self._cancel_input(input_task)
            return work_task.result(), None
        await _cancel(work_task)
        return None, input_task.result()

    async def _cancel_input(self, task: asyncio.Task) -> None:
        await _cancel(task)
        if task.done() and not task.cancelled():
            self.inputs.unget(task.result())  # it arrived anyway: keep it


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
