"""Listening: mic, wake word, voice detection, speech-to-text (spec 6.1-6.3).

    Mic (16 kHz, 80 ms frames) ──► WakeWord  ("hey jarvis", CPU)
                               └─► StreamingVad + Endpointer (when to stop)
                                       └─► Transcriber (faster-whisper, CUDA or CPU)

`Ears` puts these together for the main loop. While Jarvis answers, the ears keep
listening for barge-in (spec 6.6): the wake word, or (in `voice` mode) you talking over
it. Audio stays in memory. It's only written to disk (logs/audio/) when `debug: true`.

Heavy native libraries (ctranslate2, onnxruntime) are imported at the top and loaded on
the main thread. Loading their DLLs from a worker thread froze the app on Windows
(see the yfinance note in README.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import re
import sys
import time
import wave
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as ort
import sounddevice as sd
from faster_whisper import WhisperModel
from faster_whisper.vad import get_assets_path
from openwakeword.model import Model as WakeModel

from jarvis import audio
from jarvis.config import Stt
from jarvis.models import WHISPER_DIR, WakeFiles

log = logging.getLogger("jarvis.voice_in")

SAMPLE_RATE = 16_000
FRAME = 1280  # 80 ms: openWakeWord's frame size
FRAME_SECONDS = FRAME / SAMPLE_RATE

OnPartial = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Pure logic (tested)
# ---------------------------------------------------------------------------
class Heard(Enum):
    WAITING = "waiting"  # no speech yet
    SPEAKING = "speaking"
    DONE = "done"  # speech, then enough quiet
    TIMEOUT = "timeout"  # nobody spoke in time


@dataclass
class Endpointer:
    """Decides, frame by frame, when someone has started and finished talking."""

    start_timeout: float  # seconds to wait for speech to begin
    silence: float = 0.7  # seconds of quiet that end it (spec 6.2)
    max_seconds: float = 20.0
    min_speech: float = 0.24  # shorter bursts (a cough, a click) don't count
    frame_seconds: float = FRAME_SECONDS
    start_frame: int | None = field(default=None, init=False)
    _frames: int = field(default=0, init=False)
    _speech: int = field(default=0, init=False)
    _quiet: int = field(default=0, init=False)

    def feed(self, is_speech: bool) -> Heard:
        self._frames += 1
        if is_speech:
            if self.start_frame is None:
                self.start_frame = self._frames - 1
            self._speech += 1
            self._quiet = 0
        elif self.start_frame is not None:
            self._quiet += 1
        if self.start_frame is None:
            elapsed = self._frames * self.frame_seconds
            return Heard.TIMEOUT if elapsed >= self.start_timeout else Heard.WAITING
        return self._while_speaking()

    def _while_speaking(self) -> Heard:
        if self._quiet * self.frame_seconds >= self.silence:
            if self._speech * self.frame_seconds >= self.min_speech:
                return Heard.DONE
            self.start_frame, self._speech, self._quiet = None, 0, 0  # just noise
            return Heard.WAITING
        if self._frames * self.frame_seconds >= self.max_seconds:
            return Heard.DONE
        return Heard.SPEAKING


class TalkOver:
    """Barge-in by voice: enough steady speech while Jarvis is talking."""

    def __init__(self, min_speech: float = 0.32, frame_seconds: float = FRAME_SECONDS) -> None:
        self.need = math.ceil(min_speech / frame_seconds - 1e-9)
        self.run = 0  # speech frames in a row, while Jarvis talks

    def feed(self, is_speech: bool, jarvis_talking: bool) -> bool:
        self.run = self.run + 1 if is_speech and jarvis_talking else 0
        return self.run >= self.need


_WAKE_PREFIX = re.compile(
    r"^\W*(?:(?:hey|hi|okay|ok|yo|a)\W+)?(?:jarvis|jervis|travis|jarvi)\b\W*", re.IGNORECASE
)
# Whisper's favourite things to "hear" in silence.
_HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye"}


def clean_transcript(text: str) -> str:
    """Drop the wake phrase and whisper's silence hallucinations."""
    text = _WAKE_PREFIX.sub("", text.strip()).strip()
    if re.sub(r"[^\w\s]", "", text).strip().lower() in _HALLUCINATIONS:
        return ""
    return text[:1].upper() + text[1:]


_FUNCTION_WORDS = {
    "a", "an", "the", "and", "or", "but", "so", "to", "of", "in", "on", "at", "for", "is",
    "are", "was", "it", "its", "it's", "that", "this", "what", "about", "how", "i", "you",
    "your", "my", "me", "do", "did", "be", "with", "as", "by", "up", "out",
}  # fmt: skip
ECHO_MIN_WORDS = 3  # shorter than this, a real follow-up ("what about ETH?") can't be judged
ECHO_OVERLAP = 0.6


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in _FUNCTION_WORDS]


def sounds_like_echo(heard: str, jarvis_said: str) -> bool:
    """A voice barge-in that's mostly Jarvis's own words: the mic heard the speakers."""
    words = _content_words(heard)
    if len(words) < ECHO_MIN_WORDS:
        return False
    said = set(_content_words(jarvis_said))
    return sum(w in said for w in words) / len(words) >= ECHO_OVERLAP


def vocab_prompt(vocab: list[str]) -> str | None:
    """Whisper's initial_prompt: it spells these the way we want (spec 6.3)."""
    return f"Markets and crypto talk: {', '.join(vocab)}." if vocab else None


TARGET_PEAK = 0.5  # about -6 dBFS
MAX_GAIN = 20.0  # so near-silence isn't blown up into noise


def normalize(pcm: np.ndarray) -> np.ndarray:
    """Bring quiet speech up to a steady level before whisper. The laptop mic peaked
    around -30 dBFS, and normalizing lifted vocab hits from 81% to 85% (DECISIONS.md)."""
    peak = float(np.max(np.abs(pcm))) if pcm.size else 0.0
    if peak <= 0:
        return pcm
    return (pcm * min(MAX_GAIN, TARGET_PEAK / peak)).astype(np.float32)


# ---------------------------------------------------------------------------
# Mic
# ---------------------------------------------------------------------------
class Mic:
    """`async with Mic(device) as mic:` then `await mic.read()` for 80 ms int16 frames."""

    def __init__(self, device: int | None, max_seconds: float = 30) -> None:
        self.device = device
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(int(max_seconds / FRAME_SECONDS))
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def __aenter__(self) -> Mic:
        self._loop = asyncio.get_running_loop()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            device=self.device,
            callback=self._callback,
            **audio.stream_settings(self.device),
        )
        self._stream.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def _callback(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        assert self._loop
        self._loop.call_soon_threadsafe(self._push, indata[:, 0].copy())

    def _push(self, frame: np.ndarray) -> None:
        if self.queue.full():  # nobody's listening: drop the oldest
            self.queue.get_nowait()
        self.queue.put_nowait(frame)

    async def read(self) -> np.ndarray:
        return await self.queue.get()

    def clear(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class StreamingVad:
    """Silero VAD v6 (the ONNX file faster-whisper ships), fed 80 ms at a time."""

    CHUNK = 512
    CONTEXT = 64

    def __init__(self) -> None:
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        path = os.path.join(get_assets_path(), "silero_vad_v6.onnx")
        self.session = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), np.float32)
        self._c = np.zeros((1, 1, 128), np.float32)
        self._context = np.zeros(self.CONTEXT, np.float32)
        self._rest = np.zeros(0, np.float32)

    def prob(self, frame: np.ndarray) -> float:
        """Highest speech probability in this frame (float32 samples)."""
        buf = np.concatenate([self._rest, frame])
        best = 0.0
        while len(buf) >= self.CHUNK:
            chunk, buf = buf[: self.CHUNK], buf[self.CHUNK :]
            x = np.concatenate([self._context, chunk])[None, :]
            out, self._h, self._c = self.session.run(None, {"input": x, "h": self._h, "c": self._c})
            self._context = chunk[-self.CONTEXT :]
            best = max(best, float(out[0]))
        self._rest = buf
        return best


class WakeWord:
    """openWakeWord on CPU. `sensitivity` 0..1: higher wakes more easily."""

    def __init__(self, files: WakeFiles, sensitivity: float) -> None:
        self.model = WakeModel(
            wakeword_models=[str(files.model)],
            inference_framework="onnx",
            melspec_model_path=str(files.melspec),
            embedding_model_path=str(files.embedding),
        )
        self.threshold = min(0.95, max(0.05, 1 - sensitivity))

    def score(self, frame: np.ndarray) -> float:
        """0..1 confidence that this frame ends the wake phrase."""
        return float(max(self.model.predict(frame).values(), default=0))

    def heard(self, frame: np.ndarray) -> bool:
        return self.score(frame) >= self.threshold

    def reset(self) -> None:
        self.model.reset()


def _add_nvidia_dlls() -> None:
    """cuBLAS and cuDNN come from pip wheels (nvidia-*-cu12). Put their DLLs on the path."""
    if sys.platform != "win32":
        return
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for bin_dir in root.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    """faster-whisper. Tries CUDA first (with `device: auto`) and falls back to CPU int8."""

    COMPUTE = {"cuda": "int8_float16", "cpu": "int8"}
    BEAM = {"cuda": 5, "cpu": 1}

    def __init__(self, model: WhisperModel, device: str, prompt: str | None) -> None:
        self.model = model
        self.device = device
        self.prompt = prompt

    @classmethod
    def load(cls, cfg: Stt) -> Transcriber:
        """Load and warm up on the main thread, so every DLL is in before workers start."""
        devices = ["cuda", "cpu"] if cfg.device == "auto" else [cfg.device]
        error: Exception | None = None
        for device in devices:
            try:
                if device == "cuda":
                    _add_nvidia_dlls()
                model = WhisperModel(
                    cfg.model,
                    device=device,
                    compute_type=cls.COMPUTE[device],
                    download_root=str(WHISPER_DIR),
                )
                stt = cls(model, device, vocab_prompt(cfg.vocab))
                stt.transcribe(np.zeros(SAMPLE_RATE, np.float32))  # loads cuBLAS/cuDNN now
                return stt
            except Exception as e:
                log.warning("whisper on %s failed: %s", device, e)
                error = e
        raise RuntimeError(f"Could not load whisper {cfg.model}: {error}") from error

    @property
    def fast(self) -> bool:
        """Fast enough to also show partial transcripts while you talk."""
        return self.device == "cuda"

    def transcribe(self, pcm: np.ndarray, quick: bool = False) -> str:
        segments, _ = self.model.transcribe(
            normalize(pcm),
            language="en",
            beam_size=1 if quick else self.BEAM[self.device],
            initial_prompt=self.prompt,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,  # we already cut the audio
        )
        return "".join(s.text for s in segments).strip()


# ---------------------------------------------------------------------------
# Ears: the pieces together
# ---------------------------------------------------------------------------
PARTIAL_EVERY_SECONDS = 0.8
WAKE_PREROLL_FRAMES = 2  # keep 160 ms from before the wake fired, so no first word is lost
SPEECH_PREROLL_FRAMES = 3  # VAD lags a little; keep 240 ms before speech started
BARGE_LEAD_FRAMES = 13  # ~1 s kept while Jarvis talks, so a barge-in keeps its first words


@dataclass
class Listen:
    """Settings for one capture."""

    start_timeout: float
    silence: float
    vad_threshold: float


@dataclass
class BargeIn:
    by: Literal["wake", "voice", "ptt"]
    lead: list[np.ndarray] = field(default_factory=list)  # voice: the speech so far


class Ears:
    def __init__(
        self,
        mic: Mic,
        vad: StreamingVad,
        wake: WakeWord,
        stt: Transcriber,
        debug_dir: Path | None = None,
    ) -> None:
        self.mic = mic
        self.vad = vad
        self.wake = wake
        self.stt = stt
        self.debug_dir = debug_dir
        self._recent: deque[np.ndarray] = deque(maxlen=WAKE_PREROLL_FRAMES)
        self._stt_lock = asyncio.Lock()
        self.last_speech_at: float | None = None  # monotonic time the last capture's speech ended

    def reset(self) -> None:
        """Forget anything heard while we weren't listening (like Jarvis's own voice)."""
        self.mic.clear()
        self.wake.reset()
        self._recent.clear()

    async def wait_for_wake(self) -> None:
        self.wake.reset()
        while True:
            frame = await self.mic.read()
            self._recent.append(frame)
            if self.wake.heard(frame):
                log.info("wake word")
                self.wake.reset()
                return

    async def watch_for_barge_in(
        self, by_voice: bool, vad_threshold: float, jarvis_talking: Callable[[], bool]
    ) -> BargeIn:
        """While Jarvis answers. Returns when you say the wake word or, with `by_voice`,
        when you talk over it. Runs until cancelled otherwise."""
        self.reset()
        self.vad.reset()
        recent: deque[np.ndarray] = deque(maxlen=BARGE_LEAD_FRAMES)
        talk_over = TalkOver()
        while True:
            frame = await self.mic.read()
            self._recent.append(frame)
            recent.append(frame)
            if self.wake.heard(frame):
                log.info("barge-in: wake word")
                self.wake.reset()
                return BargeIn("wake")
            if by_voice:
                speech = self.vad.prob(_to_float(frame)) >= vad_threshold
                if talk_over.feed(speech, jarvis_talking()):
                    log.info("barge-in: voice")
                    keep = talk_over.run + SPEECH_PREROLL_FRAMES
                    return BargeIn("voice", list(recent)[-keep:])

    async def capture(
        self,
        listen: Listen,
        on_partial: OnPartial | None = None,
        after_wake: bool = False,
        lead: Sequence[np.ndarray] = (),
    ) -> np.ndarray | None:
        """Record until the speaker stops. None if nobody spoke before the timeout.

        `lead` is audio already heard (a voice barge-in). It's checked as if it were live.
        """
        self.vad.reset()
        ends = Endpointer(start_timeout=listen.start_timeout, silence=listen.silence)
        frames: list[np.ndarray] = list(self._recent) if after_wake else []
        head = len(frames)
        partials = _Partials(self, on_partial)
        pending = deque(lead)
        self.last_speech_at = None
        while True:
            frame = pending.popleft() if pending else await self.mic.read()
            frames.append(frame)
            is_speech = self.vad.prob(_to_float(frame)) >= listen.vad_threshold
            if is_speech:
                self.last_speech_at = time.monotonic()
            state = ends.feed(is_speech)
            if state is Heard.TIMEOUT:
                await partials.finish()
                return None
            if state is Heard.SPEAKING:
                partials.maybe(frames)
            if state is Heard.DONE:
                break
        await partials.finish()
        start = 0 if after_wake else max(0, head + (ends.start_frame or 0) - SPEECH_PREROLL_FRAMES)
        return self._done(frames[start:])

    async def record(self, until: asyncio.Event, on_partial: OnPartial | None = None) -> np.ndarray:
        """Push-to-talk: record until `until` is set (or 30 s)."""
        frames: list[np.ndarray] = []
        partials = _Partials(self, on_partial)
        limit = int(30 / FRAME_SECONDS)
        while not until.is_set() and len(frames) < limit:
            with contextlib.suppress(TimeoutError):
                frames.append(await asyncio.wait_for(self.mic.read(), timeout=0.2))
                partials.maybe(frames)
        await partials.finish()
        return self._done(frames)

    async def transcribe(self, pcm: np.ndarray, quick: bool = False) -> str:
        async with self._stt_lock:
            return await asyncio.to_thread(self.stt.transcribe, _to_float(pcm), quick)

    def _done(self, frames: list[np.ndarray]) -> np.ndarray:
        pcm = np.concatenate(frames) if frames else np.zeros(0, np.int16)
        if self.debug_dir and len(pcm):
            self._save(pcm)
        return pcm

    def _save(self, pcm: np.ndarray) -> None:
        """Debug mode only (spec 9)."""
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path = self.debug_dir / f"{datetime.now():%Y%m%d-%H%M%S}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        log.info("debug audio saved to %s", path)


class _Partials:
    """While you talk, re-transcribe what's been said so far (GPU only) for the HUD."""

    def __init__(self, ears: Ears, on_partial: OnPartial | None) -> None:
        self.ears = ears
        self.on_partial = on_partial if ears.stt.fast else None
        self.every = int(PARTIAL_EVERY_SECONDS / FRAME_SECONDS)
        self.last = 0
        self.task: asyncio.Task | None = None

    def maybe(self, frames: list[np.ndarray]) -> None:
        if not self.on_partial or len(frames) - self.last < self.every:
            return
        if self.task and not self.task.done():
            return
        self.last = len(frames)
        self.task = asyncio.create_task(self._run(np.concatenate(frames)))

    async def _run(self, pcm: np.ndarray) -> None:
        try:
            if text := clean_transcript(await self.ears.transcribe(pcm, quick=True)):
                await self.on_partial(text)
        except Exception:
            log.exception("partial transcript failed")

    async def finish(self) -> None:
        if self.task:
            await self.task


def _to_float(pcm: np.ndarray) -> np.ndarray:
    return pcm.astype(np.float32) / 32768.0
