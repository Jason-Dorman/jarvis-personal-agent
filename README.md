# Jarvis

A local voice assistant for markets, crypto, and fintech. Say "Hey Jarvis," ask a question, and it checks real data and answers out loud in a few sentences, with the context that matters. A browser HUD shows the same data as panels while it talks.

It's read-only: no trading, no transfers, no messages. Audio stays on your machine. Only the transcribed text goes to Claude.

- What it does: [docs/jarvis-spec.md](docs/jarvis-spec.md)
- What's built so far: [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md)
- Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)

## Setup

Runs on native Windows 11 (PowerShell). It should also work on Linux and macOS.

1. Install [uv](https://docs.astral.sh/uv/). On Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Install dependencies. uv downloads Python 3.12 for you if you don't have it. This includes NVIDIA's cuBLAS and cuDNN as pip packages (about 1 GB) so whisper can use the GPU. No CUDA toolkit install is needed, just a recent NVIDIA driver.
   ```bash
   uv sync
   ```
3. Add your keys:
   ```bash
   copy .env.example .env     # then edit .env
   ```
4. Edit your watchlist and theses in `notes/watchlist.md` and `notes/theses.md`. The format is described at the top of each file.
5. Settings (wake word sensitivity, audio devices, models, brief threshold, news feeds) live in `config.yaml`.
6. Optional: download the voice models now instead of on first run (Piper voice, wake word, whisper `small.en`, about 550 MB, into `models/`):
   ```bash
   uv run python -m jarvis.models
   ```

## Run

```bash
uv run python -m jarvis.main             # full voice + HUD
uv run python -m jarvis.main --text      # typed REPL + HUD, no audio
uv run python -m jarvis.brief            # print and save a morning brief, no HUD
uv run python hud/server.py --demo       # HUD only, with test turns
uv run python -m jarvis.models           # download voice models up front
uv run python -m jarvis.checks timings   # where each turn's time went (spec 10)
uv run python -m jarvis.checks vocab     # read finance phrases aloud, score speech-to-text
uv run python -m jarvis.checks wake --minutes 60   # count false wakes in room noise
```

The HUD is at http://localhost:8765. See [hud/README.md](hud/README.md) for how the screen is wired.

**Voice mode.** Say "Hey Jarvis" and ask. A chime means it's listening, and it stops after 0.7 s of quiet. For 8 seconds after it answers you can follow up without the wake word. You can also hold **Ctrl+Alt+Space** anywhere, or Space in the HUD (push-to-talk), or type in the HUD or the terminal. Everything goes to one queue and is answered one at a time. Type `quit` to stop. At startup it prints which mic and speakers it's using, whether whisper is on CUDA or CPU, and how to interrupt.

**Interrupting.** Say "Hey Jarvis" or press push-to-talk while it's talking or working. It stops mid-word and listens. With headphones, set `barge_in: voice` in `config.yaml` and you can just talk over it.

**Timing.** Every turn's timing goes to `logs/jarvis.log` and the `turn_timings` table. `uv run python -m jarvis.checks timings` shows medians against the spec's targets.

**Text mode** (`--text`) is the same without audio. Questions come from the terminal or the HUD.

Try: "good morning" (Jarvis greets you by the name in `address_as`), "how's USDC holding up?", "what moved on my watchlist?", "give me my morning brief", "note that I think tokenized treasuries hit $20B by year end". Add "think hard" or "deep dive" to a question to use Opus for that one answer.

Briefs are saved to `briefs/YYYY-MM-DD.md`. Saved thoughts go to `notes/theses.md` (theses) or `notes/inbox.md` (everything else).

## Gotchas

- **The brain needs `ANTHROPIC_API_KEY` in `.env`.** The Agent SDK ships its own Claude Code CLI, so there's nothing else to install.
- **"Port 8765 is busy"** means another Jarvis or `hud/server.py` is still running. Close it, or use `--port`.
- **Stock prices "unavailable".** Yahoo Finance (via yfinance) is unofficial and sometimes slow. Jarvis gives each request 10 seconds, then says the price is unavailable rather than guessing. yfinance keeps a cache in `%LOCALAPPDATA%\py-yfinance`. If a stuck Python process holds it, stock lookups time out until that process is closed.
- **yfinance must be imported on the main thread on Windows.** Importing it lazily in a worker thread froze the whole app (numpy's DLL load blocks every new thread). `tools/markets.py` imports it at the top for this reason. Keep it that way.
- **Stuck at "Starting the brain..." in Git Bash.** Fixed: output used to sit in a buffer, because Git Bash hands Python a pipe instead of a console. Jarvis now line-buffers its output. If an old copy looks stuck, it's probably already listening.
- **Wrong mic or speakers.** Set `audio.input_device` / `audio.output_device` in `config.yaml` to part of the device's name (for example `"USB Audio"`). If nothing matches, startup fails and lists the devices it found. Windows devices run at 48 kHz. Jarvis opens them through WASAPI with auto-convert, so that's fine.
- **`cublas64_12.dll is not found`** means the NVIDIA pip packages are missing. Run `uv sync`. Jarvis adds their DLL folders to the path itself. If CUDA still fails, whisper falls back to CPU and startup prints `on CPU`. That works, but it's slower, and partial transcripts are off. Force a device with `stt.device: cpu` or `cuda`.
- **Native models load on the main thread.** Whisper, Piper, and the wake word are loaded and warmed up at startup, before any worker threads run. Same reason as the yfinance note above. Keep it that way.
- **Hugging Face symlink warning** on the first whisper download: harmless on Windows without Developer Mode. Files are copied instead of linked.
- **It hears the wrong words.** Add the word to `stt.vocab`. To check what the mic actually got, set `debug: true` (each captured question is saved to `logs/audio/` as a WAV), then set it back.
- **Wakes too often, or not enough.** Change `wake_word.sensitivity` (higher wakes more easily). `uv run python -m jarvis.checks wake --minutes 60` counts false wakes at several thresholds, so you can pick one.
- **It keeps interrupting itself.** That's `barge_in: voice` on speakers: the mic hears Jarvis. Jarvis spots this, switches itself to the wake word until restart, and prints a note. Use headphones for `voice`, or keep `barge_in: wake_word`.
- **"ctrl+alt+space is already taken by another app".** Another program registered that combo. Set `push_to_talk_hotkey` to another combo (for example `"ctrl+alt+j"` or `"f9"`), or `null` to turn it off. The HUD's hold-Space still works.
- **Speech-to-text accuracy.** `uv run python -m jarvis.checks vocab` shows each phrase, you read it aloud, and it scores how many finance words came through (target 95%). Try `--model medium.en` to compare a bigger model. Add `--piper` to have Jarvis read them through the speakers instead (a rough self-test: a voice from the speakers is harder to hear than you are).
- **Something odd?** Check `logs/jarvis.log`. Every tool call (inputs, outputs, errors, timing) is also in the `tool_calls` table of `jarvis.db`.

## Develop

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```

Runtime files are gitignored: `jarvis.db` (SQLite: price snapshots, tool call log, conversation log), `briefs/`, `logs/`, and `models/` (downloaded voice models).
