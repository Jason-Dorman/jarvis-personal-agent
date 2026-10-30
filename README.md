# Jarvis

A local voice assistant for markets, crypto, and fintech. Say "Hey Jarvis," ask a question, and it checks real data and answers out loud in a few sentences. A browser HUD shows the same data as panels while it talks.

It's read-only: no trading, no transfers, no messages. Audio stays on your machine. Only the transcribed text goes to Claude.

- What it does: [docs/jarvis-spec.md](docs/jarvis-spec.md)
- What's built so far: [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md)
- Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)

## Setup

Runs on native Windows 11 (PowerShell). It should also work on Linux and macOS.

1. Install [uv](https://docs.astral.sh/uv/). On Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Install dependencies. uv downloads Python 3.12 for you if you don't have it.
   ```bash
   uv sync
   ```
3. Add your keys:
   ```bash
   copy .env.example .env     # then edit .env
   ```
4. Edit your watchlist and theses in `notes/watchlist.md` and `notes/theses.md`. The format is described at the top of each file.
5. Settings (wake word sensitivity, models, brief threshold, news feeds) live in `config.yaml`.

## Run

```bash
uv run python hud/server.py --demo       # HUD only, with test turns
uv run python -m jarvis.main --text      # typed REPL + HUD (Chunk 1, not built yet)
uv run python -m jarvis.main             # full voice (Chunk 2, not built yet)
uv run python -m jarvis.brief            # print and save a morning brief (Chunk 1, not built yet)
```

The HUD is at http://localhost:8765. See [hud/README.md](hud/README.md) for how it's wired.

## Develop

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```

Runtime files are gitignored: `jarvis.db` (SQLite: price snapshots, tool call log, conversation log), `briefs/`, `logs/`, and `models/`.
