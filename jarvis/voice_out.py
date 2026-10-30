"""Speaking: Piper TTS, sentence-by-sentence, with the HUD kept in sync (spec 6.5, 15.6).

Two stages run side by side, so sentence 2 is synthesized while sentence 1 plays:

    say(sentence) ──► [synth queue] ──► Piper ──► [play queue] ──► speakers
                                                                  │
                                       hud.speech when it starts ─┘ hud.level 20x/s

Barge-in (Chunk 3) will call `stop()`; playback checks it between 50 ms blocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Protocol

import numpy as np
import sounddevice as sd
from piper import PiperVoice

from jarvis import audio
from jarvis.speech import speakable

log = logging.getLogger("jarvis.voice_out")

LEVEL_HZ = 20  # hud.level rate, and the playback block size (spec 15.3)
LEVEL_GAIN = 4.0  # speech RMS is ~0.05-0.25; this maps it onto the HUD's 0..1


class SpeechHud(Protocol):
    async def state(self, value: str) -> None: ...
    async def speech(self, text: str) -> None: ...
    async def level(self, value: float) -> None: ...


def loudness(block: np.ndarray) -> float:
    """0..1 loudness of a float32 block, for the HUD's ring."""
    if block.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return min(1.0, rms * LEVEL_GAIN)


def chime(rate: int) -> np.ndarray:
    """Short rising two-note blip that says "listening"."""
    notes = []
    for freq, secs in ((660, 0.07), (990, 0.09)):
        t = np.arange(int(rate * secs)) / rate
        env = np.minimum(1, np.minimum(t, secs - t) / 0.01)  # 10 ms fade in and out
        notes.append(0.18 * env * np.sin(2 * np.pi * freq * t))
    return np.concatenate(notes).astype(np.float32)


class Speaker:
    """`async with Speaker(...) as mouth:` then `await mouth.say(s)` and `await mouth.done()`."""

    def __init__(self, voice: PiperVoice, hud: SpeechHud, device: int | None = None) -> None:
        self.voice = voice
        self.hud = hud
        self.device = device
        self.rate = voice.config.sample_rate
        self._block = self.rate // LEVEL_HZ
        self._synth_q: asyncio.Queue[str] = asyncio.Queue()
        self._play_q: asyncio.Queue[tuple[str, np.ndarray]] = asyncio.Queue()
        self._stop = threading.Event()
        self._out_lock = asyncio.Lock()
        self._stream: sd.OutputStream | None = None
        self._tasks: list[asyncio.Task] = []
        self._chime = chime(self.rate)

    @classmethod
    def load(cls, model_path: str, hud: SpeechHud, device_name: str | None) -> Speaker:
        """Load on the main thread: onnxruntime's DLLs must not load in a worker (README)."""
        voice = PiperVoice.load(model_path)
        return cls(voice, hud, audio.find_device("output", device_name))

    async def __aenter__(self) -> Speaker:
        self._stream = sd.OutputStream(
            samplerate=self.rate,
            channels=1,
            dtype="float32",
            device=self.device,
            latency="low",
            **audio.stream_settings(self.device),
        )
        self._stream.start()
        self._tasks = [
            asyncio.create_task(self._synth_worker()),
            asyncio.create_task(self._play_worker()),
        ]
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._stream:
            self._stream.stop()
            self._stream.close()

    # ---- the Voice interface (jarvis/turns.py) ----
    async def say(self, sentence: str) -> None:
        """Queue a sentence. Returns right away."""
        self._stop.clear()
        await self._synth_q.put(sentence)

    async def done(self) -> None:
        """Wait until everything queued has been spoken (or stopped)."""
        await self._synth_q.join()
        await self._play_q.join()

    # ---- other controls ----
    async def chime(self) -> None:
        await self._write(self._chime, None)

    def stop(self) -> None:
        """Cut playback now and drop anything queued."""
        self._stop.set()
        for q in (self._synth_q, self._play_q):
            while not q.empty():
                q.get_nowait()
                q.task_done()

    # ---- workers ----
    async def _synth_worker(self) -> None:
        while True:
            sentence = await self._synth_q.get()
            try:
                if text := speakable(sentence):
                    pcm = await asyncio.to_thread(self._synthesize, text)
                    if not self._stop.is_set():
                        await self._play_q.put((sentence, pcm))
            except Exception:
                log.exception("could not synthesize %r", sentence)
            finally:
                self._synth_q.task_done()

    async def _play_worker(self) -> None:
        while True:
            sentence, pcm = await self._play_q.get()
            try:
                if not self._stop.is_set():
                    await self.hud.state("speaking")
                    await self.hud.speech(sentence)  # the moment its audio starts
                    await self._write(pcm, self._level_sender())
            except Exception:
                log.exception("playback failed")
            finally:
                self._play_q.task_done()

    def _synthesize(self, text: str) -> np.ndarray:
        chunks = [c.audio_float_array for c in self.voice.synthesize(text)]
        return np.concatenate(chunks) if chunks else np.zeros(0, np.float32)

    def _level_sender(self) -> Callable[[float], None]:
        loop = asyncio.get_running_loop()

        def send(value: float) -> None:
            asyncio.run_coroutine_threadsafe(self.hud.level(value), loop)

        return send

    async def _write(self, pcm: np.ndarray, on_level: Callable[[float], None] | None) -> None:
        async with self._out_lock:
            await asyncio.to_thread(self._write_blocks, pcm, on_level)
        if on_level:
            on_level(0.0)

    def _write_blocks(self, pcm: np.ndarray, on_level: Callable[[float], None] | None) -> None:
        """Runs in a worker thread. Blocking writes, one 50 ms block at a time."""
        assert self._stream, "use `async with Speaker(...)` first"
        for start in range(0, len(pcm), self._block):
            if self._stop.is_set():
                return
            block = pcm[start : start + self._block]
            if on_level:
                on_level(loudness(block))
            self._stream.write(block.reshape(-1, 1))
