"""Where a turn's time goes, measured against the spec's targets (section 10).

Each part of Jarvis marks the moments it owns on one shared `TurnClock`:

    wake ─► chime                                  (voice loop)
    speech_end ─► endpointed ─► transcribed        (voice loop, from the ears)
    asked ─► first_text ─► done                    (turn runner)
    tool_start ─► tool_end, first_audio, answer_audio   (TimingHud, from HUD events)

`first_audio` is the first thing heard (it can be the "Checking." filler).
`answer_audio` is the first sentence of the real answer. When a turn ends, the spans
go to the log and to the `turn_timings` table. `python -m jarvis.checks timings`
summarizes them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("jarvis.timing")

# Spec 10, in milliseconds.
TARGET_WAKE_TO_CHIME = 300
TARGET_FIRST_WORD_SIMPLE = 2500  # 0 or 1 tool calls
TARGET_FIRST_WORD_TOOLS = 5000  # 2 or more


def first_word_target(tool_calls: int) -> int:
    return TARGET_FIRST_WORD_SIMPLE if tool_calls <= 1 else TARGET_FIRST_WORD_TOOLS


# span name → (from mark, to mark)
SPANS: dict[str, tuple[str, str]] = {
    "wake_to_chime": ("wake", "chime"),
    "endpoint": ("speech_end", "endpointed"),
    "stt": ("endpointed", "transcribed"),
    "brain_first_text": ("asked", "first_text"),
    "tools": ("tool_start", "tool_end"),
    "text_to_audio": ("first_text", "answer_audio"),
}


class TimingSink(Protocol):
    def log_timing(self, record: dict[str, Any]) -> None: ...


@dataclass
class _Turn:
    marks: dict[str, float]
    tool_calls: int = 0


class TurnClock:
    """Marks for the turn in progress. The first mark of a turn starts it."""

    def __init__(self, sink: TimingSink | None = None, now: Callable[[], float] = time.monotonic):
        self.sink = sink
        self.now = now
        self._turn: _Turn | None = None

    def mark(self, name: str, at: float | None = None, *, latest: bool = False) -> None:
        """Record when `name` happened. Keeps the first time unless `latest`."""
        if self._turn is None:
            self._turn = _Turn({})
        if latest or name not in self._turn.marks:
            self._turn.marks[name] = self.now() if at is None else at

    def tool_called(self) -> None:
        self.mark("tool_start")
        assert self._turn
        self._turn.tool_calls += 1

    def discard(self) -> None:
        """Nothing came of it (nobody spoke after the wake word)."""
        self._turn = None

    def finish(self, question: str = "", model: str | None = None, interrupted: bool = False):
        """Close the turn: log it, save it, and return the record (None if no turn)."""
        if self._turn is None:
            return None
        self.mark("done")
        record = build_record(self._turn.marks, self._turn.tool_calls)
        record.update(question=question, model=model, interrupted=interrupted)
        self._turn = None
        log.info("timing %s", _summary(record))
        if self.sink:
            try:
                self.sink.log_timing(record)
            except Exception:
                log.exception("could not save turn timing")
        return record


def source_of(marks: dict[str, float]) -> str:
    if "wake" in marks:
        return "wake"
    if "ptt" in marks:
        return "ptt"
    if "speech_end" in marks:
        return "follow_up"
    return "typed"


def build_record(marks: dict[str, float], tool_calls: int) -> dict[str, Any]:
    """Spans in whole ms. The first-word spans count from when you stopped talking
    (or from when a typed question was asked)."""

    def ms(a: str, b: str) -> int | None:
        if a in marks and b in marks:
            return round((marks[b] - marks[a]) * 1000)
        return None

    start = "speech_end" if "speech_end" in marks else "asked"
    spans = {name: ms(a, b) for name, (a, b) in SPANS.items()}
    spans["first_audio"] = ms(start, "first_audio")
    spans["answer_audio"] = ms(start, "answer_audio")
    spans["total"] = ms(start, "done")
    return {
        "source": source_of(marks),
        "tool_calls": tool_calls,
        "spans": {k: v for k, v in spans.items() if v is not None},
    }


def _summary(record: dict[str, Any]) -> str:
    spans = " ".join(f"{k}={v}" for k, v in record["spans"].items())
    return f"[{record['source']}, {record['tool_calls']} tools] {spans}"


class TimingHud:
    """Wraps the HUD and marks the moments it sees: tool calls and audio starting.

    In voice mode `speech` goes out when a sentence starts playing, which is exactly
    the first-word moment. Everything else passes straight through.
    """

    def __init__(self, hud: Any, clock: TurnClock, filler: str | None = None) -> None:
        self._hud = hud
        self._clock = clock
        self._filler = filler

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hud, name)

    async def tool_call(self, tool: str, fn: str, args: dict | None = None) -> None:
        self._clock.tool_called()
        await self._hud.tool_call(tool, fn, args)

    async def tool_result(self, tool: str) -> None:
        self._clock.mark("tool_end", latest=True)
        await self._hud.tool_result(tool)

    async def card(self, **card: Any) -> None:
        self._clock.mark("tool_end", latest=True)
        await self._hud.card(**card)

    async def speech(self, text: str) -> None:
        self._clock.mark("first_audio")
        if text != self._filler:
            self._clock.mark("answer_audio")
        await self._hud.speech(text)
