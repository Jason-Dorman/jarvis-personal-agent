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
uv run python -m jarvis.main --text      # typed REPL + HUD
uv run python -m jarvis.brief            # print and save a morning brief, no HUD
uv run python hud/server.py --demo       # HUD only, with test turns
uv run python -m jarvis.main             # full voice (Chunk 2, not built yet)
```

The HUD is at http://localhost:8765. In `--text` mode you can type questions in the terminal or in the HUD. Both go to the same queue. Type `quit` to stop. See [hud/README.md](hud/README.md) for how the screen is wired.

Try: "how's USDC holding up?", "what moved on my watchlist?", "give me my morning brief", "note that I think tokenized treasuries hit $20B by year end". Add "think hard" or "deep dive" to a question to use Opus for that one answer.

Briefs are saved to `briefs/YYYY-MM-DD.md`. Saved thoughts go to `notes/theses.md` (theses) or `notes/inbox.md` (everything else).

## Gotchas

- **The brain needs `ANTHROPIC_API_KEY` in `.env`.** The Agent SDK ships its own Claude Code CLI, so there's nothing else to install.
- **"Port 8765 is busy"** means another Jarvis or `hud/server.py` is still running. Close it, or use `--port`.
- **Stock prices "unavailable".** Yahoo Finance (via yfinance) is unofficial and sometimes slow. Jarvis gives each request 10 seconds, then says the price is unavailable rather than guessing. yfinance keeps a cache in `%LOCALAPPDATA%\py-yfinance`. If a stuck Python process holds it, stock lookups time out until that process is closed.
- **yfinance must be imported on the main thread on Windows.** Importing it lazily in a worker thread froze the whole app (numpy's DLL load blocks every new thread). `tools/markets.py` imports it at the top for this reason. Keep it that way.
- **Something odd?** Check `logs/jarvis.log`. Every tool call (inputs, outputs, errors, timing) is also in the `tool_calls` table of `jarvis.db`.

## Develop

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```

Runtime files are gitignored: `jarvis.db` (SQLite: price snapshots, tool call log, conversation log), `briefs/`, `logs/`, and `models/`.
