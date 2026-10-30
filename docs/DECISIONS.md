# Decisions and Open Questions

The PO (product owner, the user) makes product calls. Record them here so nobody has to re-ask. Newest at the top of each section.

Format: **Decision**, then why, then the date.

---

## Decided

- **Config is pydantic, strict.** `jarvis/config.py` loads `config.yaml` into frozen pydantic models and rejects unknown keys, so typos fail at startup. Added `theses_file` and `debug` keys next to the spec's list. Fixed data paths (`jarvis.db`, `notes/`, `briefs/`, `logs/`) are constants in `config.py`, not settings. *Why:* pydantic already ships with FastAPI, and strict loading catches mistakes early. *(2026-10-29)*
- **The repo is a uv "virtual" project (`package = false`).** Everything runs from the repo root, so `jarvis`, `tools`, and `hud` import as top-level packages (`from hud.server import hud`). pytest gets the root on its path. *Why:* nothing to build or install for a personal app. *(2026-10-29)*
- **Memory schema.** `price_snapshots(ts, symbol, price, change_pct, source)`, `tool_calls(ts, tool, fn, inputs JSON, outputs JSON, error, duration_ms)`, `conversation(ts, session_id, role, text, model)`. All timestamps are ISO-8601 UTC. *Why:* covers spec 6.8 and the Chunk 1 logging needs, nothing more. *(2026-10-29)*
- **Jarvis runs on native Windows, not WSL2.** The PO moved the project off WSL2. Audio uses Windows devices directly through `sounddevice`, so the WSLg PulseAudio bridge risk is gone. *Why:* PO's call. *(2026-10-29)*
- **Morning brief is on demand only.** It runs when asked ("hey Jarvis, give me my morning brief", or typed). No scheduler, no 7am run. Asking twice in one day replaces that day's file. *Why:* PO preference. *(2026-10-29)*
- **Brief data comes from one `brief.get_brief_data()` tool call,** not five separate tool calls chosen by the model. *Why:* one round trip means the brief starts faster, and the content is the same every time. The tool reuses the other tools' plain functions. *(2026-10-29)*
- **Tools are in-process MCP servers** (Agent SDK). Each tool is still its own server, but there are no extra processes. *Why:* simpler to run and debug. Keeps the spec's "one server per tool" shape. *(2026-10-29)*
- **HUD events come from SDK pre/post tool hooks,** not from inside the tools. *Why:* tools stay unaware of the screen (low coupling). Cards are built from tool data, per spec 15.6. *(2026-10-29)*
- **Agent SDK built-in tools are all turned off.** Only Jarvis's MCP tools are allowed. *Why:* this enforces the read-only safety rule. *(2026-10-29)*
- **Big build chunks.** The work is split into four large chunks, not small slices. *Why:* fun personal project, speed over ceremony, fix as we go. *(2026-10-29)*

---

## Open questions for the PO

Each one has a default. Work proceeds on the default unless the PO says otherwise.

1. **Push-to-talk hotkey.** The spec says `ctrl+space` globally. Now that we're on native Windows, a global hook (e.g. `pynput`) can work. *Default:* ship the HUD's hold-Space (browser window focused) first, then add the global `ctrl+space` in Chunk 2 if it behaves.
2. **Echo cancellation for barge-in.** PulseAudio's `module-echo-cancel` isn't an option on Windows. *Default:* headphones for v1. Look at a software AEC in Chunk 3 only if barge-in on speakers matters.
3. **Watchlist and theses file format.** *Default:* `watchlist.md` is one symbol per line as `- BTC` or `- SPY (stock)`. `theses.md` uses one `## heading` per thesis with free text under it. Chunk 0 seeded both files this way, with a starter watchlist (BTC, ETH, SOL, HYPE, SPY, QQQ, COIN) and one example thesis. Swap in your own.
4. **Price data keys.** *Default:* CoinGecko's free/demo key if one is provided in `.env`, no key otherwise. Is there a paid key or a preferred source?
5. **Brief length when nothing happened.** *Default:* skip quiet sections in a single line ("All pegs normal. Watchlist quiet.") even if the brief ends up under 60 s.
