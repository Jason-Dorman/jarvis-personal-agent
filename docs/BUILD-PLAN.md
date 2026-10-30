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
- [ ] Piper with `en_US-ryan-medium`. Download the model on first run into a gitignored `models/` folder.
- [ ] Sentence splitter on the streamed reply. Speak sentence 1 while sentence 2 is still generating.
- [ ] Numbers spoken the way people say them. Prompt first, small `speakable()` cleanup pass second (tested).
- [ ] `hud.speech` when each sentence's audio *starts playing*, `hud.level` about 20 times a second from playback loudness, state `speaking` and then `idle`
- [ ] Short "listening" chime and a "Checking" filler if the first tool call runs long

### Listening (`jarvis/voice_in.py`)
- [ ] Audio capture via `sounddevice`, 16 kHz mono, on the default Windows input device (WASAPI)
- [ ] faster-whisper `small.en`. Try CUDA on the GTX 1660 Ti and fall back to CPU int8. Pass the custom vocab as `initial_prompt` / hotwords.
- [ ] Push-to-talk first: HUD hold-Space (`ptt` events) → record → transcribe → agent
- [ ] Then Silero VAD (stop after 0.7 s of silence) and openWakeWord `hey_jarvis` (always on, CPU)
- [ ] Follow-up window: keep listening for 8 s after Jarvis finishes, no wake word needed
- [ ] `hud.heard` partials and the final transcript, state `listening` then `thinking`
- [ ] Audio never written to disk unless `debug: true`

### Main loop (`jarvis/main.py`)
- [ ] One asyncio loop that owns the states: idle → listening → thinking → speaking → (follow-up) → idle
- [ ] Voice and HUD text input both feed the same turn queue

**Done when:** you can say "Hey Jarvis, how's USDC holding up?" and hear the answer, then follow up with "and Tether?" without the wake word.

---

## Chunk 3 — Feels natural

Polish against the spec's performance targets.

- [ ] Barge-in: VAD keeps running while Jarvis speaks. Speech cuts playback, cancels the in-flight reply, and starts a new turn. Needs headphones or a software echo canceller (see DECISIONS.md).
- [ ] Timing log per turn: wake → listening, end of speech → first audio, tool time. Check it against spec section 10.
- [ ] Tune: whisper model size, VAD silence, wake sensitivity, prompt length and caching, effort level on the default model
- [ ] Vocab accuracy check: read the vocab list aloud and measure hits (target 95%)
- [ ] False-wake check: an hour of normal room noise (target under 1 per hour)
- [ ] Run the v1 "done when" test and fix what breaks

**Done when:** v1's done test passes, and the latency targets are met or the misses are written down in DECISIONS.md with the reason.

---

## Risks to watch

| Risk | What we do |
|---|---|
| **Native deps on Windows.** CUDA for faster-whisper (cuDNN/cuBLAS DLLs) and Piper/openWakeWord wheels can be fiddly on Windows. | Test CUDA at the start of Chunk 2 and fall back to CPU int8 if it fights back. Write every gotcha in the root README. |
| **Echo during barge-in.** No PulseAudio `module-echo-cancel` on Windows, so speaker output can trigger the VAD. | Headphones for v1. See DECISIONS.md. |
| **Agent SDK latency.** The Agent SDK runs the Claude Code harness as a subprocess. It's heavier than a plain API call and may eat into the 2.5 s target. | Measured in Chunk 1: session start 1 to 3 s (once), then about 3 s to first text for a one-tool question. That's over the 2.5 s target but close. Tune prompt length and effort in Chunk 3 first. If it's still too slow, swap `agent.py` to the plain Anthropic SDK tool runner behind the same interface. Ask the PO first, since the spec names the Agent SDK. |
| **yfinance on Windows.** Importing it (pandas, numpy) in a worker thread froze the app: the DLL load holds the OS loader lock and blocks every new thread. Its shared SQLite cache can also stall for minutes if another process is stuck. | Fixed in Chunk 1: yfinance is imported at startup on the main thread, and Yahoo calls get a 10 s budget per request, after which the symbol comes back as unavailable. |
| **Free data API limits.** CoinGecko's free tier rate-limits, and yfinance is unofficial and flaky. | Short in-memory cache per symbol (about 30–60 s). Batch watchlist lookups. Return clean errors so Jarvis says "unavailable" instead of guessing. |
| **Piper / openWakeWord install.** Native deps and ONNX models. | Pin versions in `pyproject.toml`. Put model downloads in a setup script. Write down every gotcha in the root README as it comes up. |
