# Jarvis — Build Plan

Four chunks. Each one is big on purpose: build a whole layer, get it working end to end, fix things as we go. The spec ([jarvis-spec.md](jarvis-spec.md)) says *what*. This file says *in what order* and *what's done*.

Tick boxes as work lands, in the same change as the code. Open questions and choices live in [DECISIONS.md](DECISIONS.md).

**v1 is done when:** you can ask five real questions in a row, by voice, without touching the keyboard, and every number checks out.

---

## Chunk 0 — Scaffold (short)

Get the repo into the shape of spec section 12 so every later chunk has a place to land.

- [x] `git init`, `.gitignore` (`.env`, `jarvis.db`, `logs/`, `briefs/`, `.venv/`, model files)
- [x] `pyproject.toml` managed with `uv`, Python 3.12. Dev deps: `pytest`, `pytest-asyncio`, `ruff`
- [x] Move the HUD into `hud/`: `server.py` → `hud/server.py`, `docs/index.html` → `hud/index.html`, root `README.md` → `hud/README.md` (it's the HUD readme). `server.py` serves `HERE / "index.html"`, so it only works once they sit together.
- [x] New root `README.md`: what Jarvis is, how to set it up, how to run it
- [x] `config.yaml` (spec section 11) and `jarvis/config.py`: load once into typed settings (pydantic)
- [x] `.env.example` with `ANTHROPIC_API_KEY` and any data-source keys
- [x] `notes/watchlist.md` and `notes/theses.md` seeded with a starter format (default format from DECISIONS.md, PO can still change it)
- [x] `jarvis/memory.py`: SQLite schema for `price_snapshots`, `tool_calls`, `conversation`

**Done when:** `uv run pytest` passes (even with zero tests), `uv run python hud/server.py --demo` serves the HUD.

---

## Chunk 1 — Text Jarvis with a live HUD

The whole brain, every tool, the morning brief, and the HUD wired up. No audio yet. You type, it answers with real data, and panels appear on screen.

### Tools (`tools/`)
Each is an in-process MCP server built with the Agent SDK, so there are no extra processes to manage. Put the data-fetching logic in plain functions (for example `tools/markets.py`) and keep the MCP wrapper thin. The `brief` tool and the tests call the plain functions directly.

- [x] `markets`: `get_price`, `get_movers`, `get_watchlist_snapshot` (CoinGecko for crypto, yfinance for stocks/ETFs/rates). Writes price snapshots to SQLite.
- [x] `pegs`: `get_peg`, `get_supply_change`, `list_peg_alerts` (DefiLlama stablecoins API)
- [x] `news`: `get_headlines`, `get_article_summary` (RSS from `config.yaml`, `feedparser`)
- [x] `notes`: `read_notes`, `append_note` (only inside `notes/`, rejects path escapes)
- [x] `brief`: `get_brief_data`, which returns watchlist movers over threshold, peg status, top headlines, and the theses text in one structured result
- [x] Every tool returns structured data plus a clear `error` field on failure. It never raises into the model.
- [x] Every tool call is logged to `tool_calls` (time, inputs, outputs)

### Brain (`jarvis/agent.py`)
- [x] Claude Agent SDK session (`ClaudeSDKClient`) with conversation history for the session
- [x] **Turn off every built-in Agent SDK tool** (Bash, file edits, web, and so on). Allow only our MCP tools. This is how the read-only rule is enforced.
- [x] System prompt with the voice rules (spec 6.4), the brief order (spec 8), and the contents of `watchlist.md` and `theses.md` loaded at session start
- [x] Model switch: Sonnet by default, Opus when the user says "think hard" or "deep dive"
- [x] Stream the reply text out as it arrives. Chunk 2 hangs TTS off this stream.
- [x] Brief detection: if `brief.get_brief_data` was called during a turn, save the final reply to `briefs/YYYY-MM-DD.md`
- [x] Log each turn to `conversation`

### HUD wiring
- [x] Wrap every tool call: `hud.tool_call` before it runs, `hud.card` (or `hud.tool_result`) after. Use the SDK's pre/post tool hooks so the tools don't know the HUD exists.
- [x] `jarvis/cards.py`: one card builder per (tool, fn), built from tool data only (spec 15.6)
- [x] Tell the brain which panels are on screen and their keys
- [x] Typed questions from `hud.inbox` drive the agent. Without audio, `speech` events go out per sentence as text is generated.
- [x] Ticker: push the watchlist snapshot at startup and every few minutes

### Entry points
- [x] `uv run python -m jarvis.main --text`: terminal REPL plus HUD
- [x] `uv run python -m jarvis.brief`: prints the brief and saves it

### Tests
- [x] Each tool's parsing against saved API responses in `tests/fixtures/` (no live network in tests)
- [x] Card builders, brief data assembly, notes path safety

**Done when:** you type "how's USDC holding up?", "what moved on my watchlist?", "give me my morning brief", and "note that…" in the HUD. Each one returns real numbers, the panels appear, and a brief file is saved.

**Status: done (2026-10-29).** All four questions pass end to end through the HUD WebSocket with live data. Measured latency, typed question to first text: about 3 s for a one-tool question, about 7 s for the brief, about 8 s for an Opus "think hard" turn with three tools. Chunk 3 tunes this against spec 10.

---

## Chunk 2 — Voice in and out

Make it talk and listen. This is the biggest chunk and the one most likely to fight back (see Risks).

### Speaking (`jarvis/voice_out.py`)
- [x] Piper with `en_US-ryan-medium`. Download the model on first run into a gitignored `models/` folder.
- [x] Sentence splitter on the streamed reply. Speak sentence 1 while sentence 2 is still generating.
- [x] Numbers spoken the way people say them. Prompt first, small `speakable()` cleanup pass second (tested).
- [x] `hud.speech` when each sentence's audio *starts playing*, `hud.level` about 20 times a second from playback loudness, state `speaking` and then `idle`
- [x] Short "listening" chime and a "Checking" filler if the first tool call runs long

### Listening (`jarvis/voice_in.py`)
- [x] Audio capture via `sounddevice`, 16 kHz mono, on the default Windows input device (WASAPI)
- [x] faster-whisper `small.en`. Try CUDA on the GTX 1660 Ti and fall back to CPU int8. Pass the custom vocab as `initial_prompt` / hotwords.
- [x] Push-to-talk first: HUD hold-Space (`ptt` events) → record → transcribe → agent
- [x] Then Silero VAD (stop after 0.7 s of silence) and openWakeWord `hey_jarvis` (always on, CPU)
- [x] Follow-up window: keep listening for 8 s after Jarvis finishes, no wake word needed
- [x] `hud.heard` partials and the final transcript, state `listening` then `thinking`
- [x] Audio never written to disk unless `debug: true`

### Main loop (`jarvis/conversation.py`, started by `jarvis/main.py`)
- [x] One asyncio loop that owns the states: idle → listening → thinking → speaking → (follow-up) → idle
- [x] Voice and HUD text input both feed the same turn queue

**Done when:** you can say "Hey Jarvis, how's USDC holding up?" and hear the answer, then follow up with "and Tether?" without the wake word.

**Status: built and running (2026-10-29). Waiting on the PO's own voice test.** Tested by playing Piper's voice out of the laptop speakers into the laptop mic. "Hey Jarvis!" woke it, "How's USDC holding up?" was heard exactly, and the answer was spoken with live data. The follow-up with no wake word was captured and answered with Tether's real peg, though "And Tether?" came through as "That's Tether." The recording was clean but quiet (about −30 dB from speaker to mic). Push-to-talk (HUD hold-Space) and typed questions also work in voice mode. `--text` is unchanged.

Measured on the GTX 1660 Ti: whisper `small.en` on CUDA transcribes a question in about 0.3 s. Wake word costs about 2 ms and VAD about 0.4 ms per 80 ms of audio. Piper runs about 20 times faster than real time. End of speech to first spoken word is about 5 s for a one-tool question: about 1 s for endpointing and transcription, the rest in the brain. "Checking." covers most of that gap when the tool is slow. The CPU fallback for whisper is coded but wasn't exercised, since CUDA worked.

---

## Chunk 3 — Feels natural

Polish against the spec's performance targets.

- [x] Barge-in: the ears keep listening while Jarvis works or speaks. The wake word or push-to-talk cuts playback, cancels the in-flight reply (drained in the background), and starts a new turn. `barge_in: voice` also lets plain speech interrupt, with headphones (DECISIONS.md). No software echo canceller.
- [x] Timing log per turn: wake → chime, endpointing, speech-to-text, brain, tools, first word, answer start. Goes to the log and `turn_timings`. `python -m jarvis.checks timings` compares against spec 10.
- [x] Latency: tuned, and the miss is written down (PO's call to stay on Sonnet + Agent SDK at effort medium). "Checking." now plays when the first tool call starts. Data is warmed on wake. First word median 2.97 s vs the 2.5 s target, answer start median 4.8 s. Text after a tool result arrives in one burst from the API, so it can't stream.
- [x] Reply length: the PO wants more detail, so the cap is now 5 sentences, with context drawn from tool data. The prompt calls it a hard limit.
- [x] Greeting (PO request): "Hey Jarvis, good morning" gets "Good morning, Mr. Dorman." right away, without calling Claude. The name is `address_as`.
- [x] Global push-to-talk hotkey: Ctrl+Alt+Space through RegisterHotKey. It also interrupts.
- [x] Tune: whisper stays `small.en` (`medium.en` wasn't better on vocab) with level normalization added, VAD silence stays 0.7 s, effort stays medium. Wake sensitivity stays 0.5, since the false-wake runs never came close.
- [ ] Vocab accuracy check: built (`python -m jarvis.checks vocab`). Self-test with Piper's voice through the speakers: 85% vs the 95% target, with misses on USDT↔USDC and the short "And Tether?". **Needs a run with the PO's own voice.**
- [x] False-wake check: 0 wakes in 23 min of a quiet room at night, and 0 in 4.2 min of continuous finance speech from the speakers. The highest wake score in the speech run was 0.01, against a threshold of 0.5. The quiet-room run was stopped early, since a quiet room at night says little. A full hour during a normal day (TV, people talking) is still worth doing: `python -m jarvis.checks wake --minutes 60`.
- [x] v1 "done when" test, using Piper's voice through the speakers: five questions in a row by voice, one wake word then four follow-ups, no keyboard. Every spoken number matched the tool results. Two of the five were misheard (USDC → "this cold", Tether → "T-bills"), and Jarvis answered what it heard and named its guess. **Needs a run with the PO's own voice.**

**Done when:** v1's done test passes, and the latency targets are met or the misses are written down in DECISIONS.md with the reason.

**Status: built (2026-10-30). Waiting on the PO's own voice for the vocab and done tests.** Everything ran on laptop speakers and mic, driven by Piper's voice. That's a harsh stand-in for a person at the mic: peaks around −30 dBFS, and recognition is noticeably worse than with a live voice in Chunk 2. Barge-in was tested by wake word (during the brief) and by hotkey (injected key events), and `voice` mode was tested on speakers, where it loops on its own echo. That loop led to the echo guard. The latency misses and their reasons are in DECISIONS.md and spec 10.

---

## Risks to watch

| Risk | What we do |
|---|---|
| **Native deps on Windows.** CUDA for faster-whisper (cuDNN/cuBLAS DLLs) and Piper/openWakeWord wheels can be fiddly on Windows. | Done in Chunk 2. CUDA works through NVIDIA's pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), so there's no system CUDA install. `voice_in.py` adds their DLL folders to the path and warms whisper up on the main thread. CPU int8 is the fallback. Gotchas are in the root README. |
| **Echo during barge-in.** No PulseAudio `module-echo-cancel` on Windows, so speaker output can trigger the VAD. | Handled in Chunk 3: the default barge-in is the wake word, which works on speakers. Plain-voice barge-in needs headphones, and an echo guard switches it off if Jarvis hears itself. |
| **Agent SDK latency.** The Agent SDK runs the Claude Code harness as a subprocess. It's heavier than a plain API call and may eat into the 2.5 s target. | Measured in Chunk 3: the harness isn't the main cost. Two API round trips are, and the post-tool reply comes in one burst even from the raw API. The PO chose to keep the Agent SDK and write the miss down (DECISIONS.md). A plain-API swap is still an option behind the same `Brain` interface. |
| **yfinance on Windows.** Importing it (pandas, numpy) in a worker thread froze the app: the DLL load holds the OS loader lock and blocks every new thread. Its shared SQLite cache can also stall for minutes if another process is stuck. | Fixed in Chunk 1: yfinance is imported at startup on the main thread, and Yahoo calls get a 10 s budget per request, after which the symbol comes back as unavailable. |
| **Free data API limits.** CoinGecko's free tier rate-limits, and yfinance is unofficial and flaky. | Short in-memory cache per symbol (about 30–60 s). Batch watchlist lookups. Return clean errors so Jarvis says "unavailable" instead of guessing. |
| **Piper / openWakeWord install.** Native deps and ONNX models. | Done in Chunk 2. Both installed from wheels with no build step. openWakeWord runs its ONNX models (no tflite on Windows). Models download on first run, or up front with `uv run python -m jarvis.models`. |
