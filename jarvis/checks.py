"""Measure Jarvis against its performance targets (spec 10).

    uv run python -m jarvis.checks timings            # where the time goes, per turn
    uv run python -m jarvis.checks vocab              # you read finance phrases aloud
    uv run python -m jarvis.checks vocab --piper      # Jarvis's voice reads them (self-test)
    uv run python -m jarvis.checks wake --minutes 60  # false wakes in normal room noise

Audio stays in memory, as everywhere else. Nothing is saved unless `debug: true`.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
import statistics
import sys
import time
from collections.abc import Iterable, Sequence
from typing import Any

from jarvis.timing import TARGET_WAKE_TO_CHIME, first_word_target

# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------
TIMING_ROWS = [
    ("wake_to_chime", "Wake word -> chime"),
    ("endpoint", "Stopped talking -> endpointed"),
    ("stt", "Speech-to-text"),
    ("brain_first_text", "Question -> first text from Claude"),
    ("tools", "Tool calls (wall time)"),
    ("text_to_audio", "First text -> answer audio"),
    ("first_audio", "Stopped talking -> first word"),
    ("answer_audio", "Stopped talking -> answer starts"),
    ("total", "Stopped talking -> done"),
]


def _pct(values: Sequence[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize_timings(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Median and p90 per span, plus how many turns met their first-word target."""
    records = list(records)
    rows = []
    for key, label in TIMING_ROWS:
        values = [r["spans"][key] for r in records if key in r["spans"]]
        if values:
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "n": len(values),
                    "median": round(statistics.median(values)),
                    "p90": _pct(values, 0.9),
                }
            )
    judged = [r for r in records if "first_audio" in r["spans"] and not r.get("interrupted")]
    met = [r for r in judged if r["spans"]["first_audio"] <= first_word_target(r["tool_calls"])]
    wakes = [r["spans"]["wake_to_chime"] for r in records if "wake_to_chime" in r["spans"]]
    return {
        "turns": len(records),
        "rows": rows,
        "first_word_met": len(met),
        "first_word_judged": len(judged),
        "wake_met": sum(v <= TARGET_WAKE_TO_CHIME for v in wakes),
        "wake_judged": len(wakes),
    }


def print_timings(limit: int, source: str | None) -> None:
    from jarvis.config import DB_PATH
    from jarvis.memory import Memory

    memory = Memory(DB_PATH)
    records = memory.recent_timings(limit)
    memory.close()
    if source:
        records = [r for r in records if r["source"] == source]
    if not records:
        print("No timed turns yet. Ask Jarvis a few questions first.")
        return
    s = summarize_timings(records)
    print(f"Last {s['turns']} turns" + (f" ({source})" if source else "") + ", in ms:\n")
    print(f"  {'':38} {'median':>7} {'p90':>7} {'n':>4}")
    for row in s["rows"]:
        print(f"  {row['label']:38} {row['median']:>7} {row['p90']:>7} {row['n']:>4}")
    print()
    if s["first_word_judged"]:
        print(
            f"  First word on target: {s['first_word_met']} of {s['first_word_judged']} turns"
            " (2.5 s with 0-1 tools, 5 s with 2+; 'Checking.' counts as a word)"
        )
    if s["wake_judged"]:
        print(f"  Wake -> chime under 0.3 s: {s['wake_met']} of {s['wake_judged']}")
    print("\n  Newest turns:")
    for r in records[:8]:
        spans = r["spans"]
        cut = " [interrupted]" if r["interrupted"] else ""
        print(
            f"    {r['ts'][11:19]} {r['source']:9} {r['tool_calls']} tools"
            f"  first word {spans.get('first_audio', '-')}"
            f"  answer {spans.get('answer_audio', '-')}"
            f"  {(r['question'] or '')[:40]!r}{cut}"
        )


# ---------------------------------------------------------------------------
# Vocab accuracy
# ---------------------------------------------------------------------------
# Real questions that use each vocab word. Words without phrases here get a template.
VOCAB_PHRASES: dict[str, list[str]] = {
    "USDC": ["How's USDC holding up?", "What's the USDC supply doing this week?"],
    "USDT": ["Where is USDT trading?", "And USDT?"],
    "Tether": ["And Tether?", "Is Tether still on its peg?"],
    "perps": ["What are perps funding rates doing?"],
    "RWA": ["Any news on RWA tokenization?"],
    "Hyperliquid": ["How's Hyperliquid doing today?"],
    "basis": ["What's the bitcoin basis trade paying right now?"],
    "T-bills": ["Where are T-bills yielding?"],
    "stablecoin": ["Which stablecoin grew the most this week?"],
    "watchlist": ["What moved on my watchlist?"],
}


def vocab_cases(vocab: Sequence[str]) -> list[tuple[str, str]]:
    """(phrase to say, vocab word it must contain) for every word in the vocab."""
    cases = []
    for word in vocab:
        for phrase in VOCAB_PHRASES.get(word, [f"What's the latest on {word}?"]):
            cases.append((phrase, word))
    return cases


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def vocab_hit(transcript: str, word: str) -> bool:
    """The word made it through, ignoring case, spaces, and hyphens ("T bills" counts)."""
    return _compact(word) in _compact(transcript)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace("-", " ")).split()


def same_words(transcript: str, phrase: str) -> bool:
    return _words(transcript) == _words(phrase)


async def _run_vocab(rounds: int, piper: bool, model: str | None) -> None:
    from jarvis import audio, models
    from jarvis.config import LOGS_DIR, get_config
    from jarvis.voice_in import Ears, Listen, Mic, StreamingVad, Transcriber, WakeWord

    cfg = get_config()
    stt_cfg = cfg.stt.model_copy(update={"model": model}) if model else cfg.stt
    print(f"Loading whisper {stt_cfg.model}...", flush=True)
    stt = Transcriber.load(stt_cfg)
    wake = WakeWord(models.wake_files(cfg.wake_word.model), cfg.wake_word.sensitivity)
    voice = _load_piper(cfg.tts.voice) if piper else None
    out_device = audio.find_device("output", cfg.audio.output_device)
    debug_dir = LOGS_DIR / "audio" if cfg.debug else None
    cases = vocab_cases(cfg.stt.vocab) * rounds
    hits = exact = 0
    misses: list[tuple[str, str]] = []
    async with Mic(audio.find_device("input", cfg.audio.input_device)) as mic:
        ears = Ears(mic, StreamingVad(), wake, stt, debug_dir)
        listen = Listen(4.0 if piper else 8.0, cfg.vad.silence_seconds, cfg.vad.threshold)
        if not piper:
            print("\nRead each phrase aloud after it appears, at your normal pace.\n")
        for i, (phrase, word) in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {phrase}", flush=True)
            ears.reset()
            capture = asyncio.create_task(ears.capture(listen))
            if voice:
                await asyncio.sleep(0.2)
                await asyncio.to_thread(_play, voice, phrase, out_device)
            pcm = await capture
            heard = (await ears.transcribe(pcm)).strip() if pcm is not None else ""
            level = f"peak {peak_dbfs(pcm):.0f} dBFS" if pcm is not None else "no speech"
            hit = vocab_hit(heard, word)
            hits += hit
            exact += same_words(heard, phrase)
            if not hit:
                misses.append((phrase, heard))
            print(f"    heard: {heard or '(nothing)'}  {'ok' if hit else 'MISS'}  ({level})")
            await asyncio.sleep(0.4)
    n = len(cases)
    print(f"\nVocab words heard: {hits}/{n} = {hits / n:.0%} (target 95%)")
    print(f"Whole phrase exact: {exact}/{n} = {exact / n:.0%}")
    for phrase, heard in misses:
        print(f"  missed: {phrase!r} -> {heard!r}")


def peak_dbfs(pcm: Any) -> float:
    """Loudest sample of int16 audio, in dB below full scale (0 is clipping)."""
    import numpy as np

    peak = float(np.max(np.abs(pcm.astype(np.float32)))) if len(pcm) else 0.0
    return 20 * math.log10(max(peak, 1.0) / 32768)


def _load_piper(name: str) -> Any:
    from piper import PiperVoice

    from jarvis import models

    return PiperVoice.load(str(models.piper_voice(name)))


def _play(voice: Any, text: str, device: int | None) -> None:
    import numpy as np
    import sounddevice as sd

    from jarvis import audio
    from jarvis.speech import speakable

    pcm = np.concatenate([c.audio_float_array for c in voice.synthesize(speakable(text))])
    try:
        sd.play(pcm, voice.config.sample_rate, device=device, **audio.stream_settings(device))
    except sd.PortAudioError:  # the device is busy in another mode: use the system default
        sd.play(pcm, voice.config.sample_rate)
    sd.wait()


# ---------------------------------------------------------------------------
# False wakes
# ---------------------------------------------------------------------------
WAKE_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)
WAKE_REFRACTORY_SECONDS = 2.0  # one wake, not several, per "hey jarvis"


def count_triggers(
    scores: Iterable[float],
    thresholds: Sequence[float] = WAKE_THRESHOLDS,
    frame_seconds: float = 0.08,
    refractory: float = WAKE_REFRACTORY_SECONDS,
) -> dict[float, int]:
    """How many separate wakes each threshold would have fired on this score stream."""
    counts = dict.fromkeys(thresholds, 0)
    quiet_until = dict.fromkeys(thresholds, -1.0)
    for i, score in enumerate(scores):
        t = i * frame_seconds
        for th in thresholds:
            if score >= th and t >= quiet_until[th]:
                counts[th] += 1
                quiet_until[th] = t + refractory
    return counts


async def _run_wake(minutes: float) -> None:
    from jarvis import audio, models
    from jarvis.config import get_config
    from jarvis.voice_in import FRAME_SECONDS, Mic, WakeWord

    cfg = get_config()
    wake = WakeWord(models.wake_files(cfg.wake_word.model), cfg.wake_word.sensitivity)
    print(
        f"Listening for {minutes:g} min. Go about your day; don't say the wake word."
        f" Current threshold {wake.threshold:.2f} (sensitivity {cfg.wake_word.sensitivity}).",
        flush=True,
    )
    scores: list[float] = []
    start = time.monotonic()
    async with Mic(audio.find_device("input", cfg.audio.input_device)) as mic:
        while time.monotonic() - start < minutes * 60:
            score = wake.score(await mic.read())
            scores.append(score)
            if score >= wake.threshold:
                elapsed = time.monotonic() - start
                print(f"  wake at {elapsed / 60:5.1f} min (score {score:.2f})", flush=True)
    hours = len(scores) * FRAME_SECONDS / 3600
    print(f"\nListened {hours * 60:.1f} min. Highest score {max(scores, default=0):.2f}.")
    for th, n in count_triggers(scores, frame_seconds=FRAME_SECONDS).items():
        mark = " <- current" if abs(th - wake.threshold) < 1e-6 else ""
        print(f"  threshold {th:.1f}: {n} wakes = {n / hours:.1f} per hour{mark}")
    print("Target: under 1 per hour (spec 10).")


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m jarvis.checks")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("timings", help="where the time goes, per turn")
    t.add_argument("--last", type=int, default=30)
    t.add_argument("--source", choices=["wake", "ptt", "follow_up", "typed"])
    v = sub.add_parser("vocab", help="speech-to-text accuracy on the vocab list")
    v.add_argument("--piper", action="store_true", help="Jarvis's voice reads the phrases")
    v.add_argument("--rounds", type=int, default=1)
    v.add_argument("--model", help="try another whisper model, like medium.en")
    w = sub.add_parser("wake", help="count false wakes in room noise")
    w.add_argument("--minutes", type=float, default=60)
    args = p.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    if args.cmd == "timings":
        print_timings(args.last, args.source)
        return
    from jarvis.runtime import setup_logging

    setup_logging(debug=False)
    if args.cmd == "vocab":
        asyncio.run(_run_vocab(args.rounds, args.piper, args.model))
    else:
        asyncio.run(_run_wake(args.minutes))


if __name__ == "__main__":
    main()
