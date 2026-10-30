# Jarvis

A local voice assistant for markets, crypto, and fintech. Wake word → speech-to-text → Claude with read-only data tools → Piper TTS, plus a browser HUD.

## Read first

- [docs/jarvis-spec.md](docs/jarvis-spec.md): what we're building (source of truth for behavior)
- [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md): build order, chunks, and status checkboxes
- [docs/DECISIONS.md](docs/DECISIONS.md): decisions made and open questions for the PO
- [docs/ENGINEERING-PRINCIPLES.md](docs/ENGINEERING-PRINCIPLES.md): code design rules

## Working with the user

- **The user is the PO (product owner). Ask them questions when you need to.** Ask when a requirement is unclear, when the spec and reality conflict, or when a choice changes something they'll see, hear, or pay for. Don't ask about implementation details you can decide yourself. Make the call and note it.
- Write every answer and new decision into [docs/DECISIONS.md](docs/DECISIONS.md) so it doesn't get re-asked.
- This is a fun personal project. Build in the big chunks from the build plan, and favor getting things working over ceremony. It's fine to fix things as we go.

## Docs move in lockstep with code

Docs are updated **in the same change** as the code, never "later." Before you call a task done, check:

- Behavior, config keys, tools, HUD events, or folder layout changed → update `docs/jarvis-spec.md`
- A build-plan item finished, or the plan changed → tick or edit `docs/BUILD-PLAN.md`
- A choice was made or a PO question was answered → `docs/DECISIONS.md`
- Setup steps, commands, or install gotchas changed → root `README.md` (and `hud/README.md` for HUD wiring)
- Commands below changed → this file

If code and docs disagree, that's a bug. Fix whichever one is wrong.

## Hard rules (from the spec)

- **Read-only.** No tool trades, transfers, or sends messages. All built-in Agent SDK tools stay off. Only Jarvis's MCP tools are allowed.
- **No made-up numbers.** Every number the user hears or sees comes from a tool result. HUD cards are built from tool data, never from model text.
- **Morning brief runs only when asked.** No scheduler.
- Secrets live in `.env` only. Audio never touches disk unless debug mode is on. Logs stay local.

## Stack and commands

Python 3.12, managed with `uv`. Claude Agent SDK for the brain (Sonnet `claude-sonnet-5-5` by default, Opus `claude-opus-5-5` for "think hard"). FastAPI + WebSocket for the HUD.

```bash
uv sync                                  # install
uv run pytest                            # tests
uv run ruff check . && uv run ruff format .
uv run python -m jarvis.main --text      # typed REPL + HUD (Chunk 1)
uv run python -m jarvis.main             # full voice (Chunk 2+)
uv run python -m jarvis.brief            # print and save a morning brief
uv run python hud/server.py --demo       # HUD only, test turns
```

HUD: http://localhost:8765

## Environment

- Native Windows 11 (moved off WSL2). Shell is PowerShell; the `uv` commands above work as-is. Audio goes through Windows directly (`sounddevice` on WASAPI), no PulseAudio.
- GPU: GTX 1660 Ti (6 GB), usable for faster-whisper via CUDA.
- Push-to-talk goes through the HUD's hold-Space for v1. A real global hotkey is possible now that we're native (see DECISIONS.md).

## Code conventions

- Follow ENGINEERING-PRINCIPLES.md, applied pragmatically.
- Tools: data logic in plain functions (testable, reused by `brief`), thin MCP wrappers around them. On failure, return a structured `error`. Never raise into the model.
- Tests: cover parsing, card building, brief assembly, number speaking, and sentence splitting. Use saved fixtures in `tests/fixtures/`, never live network. Audio glue doesn't need unit tests. Verify it by running it.
