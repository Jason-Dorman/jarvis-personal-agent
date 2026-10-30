# Decisions and Open Questions

The PO (product owner, the user) makes product calls. Record them here so nobody has to re-ask. Newest at the top of each section.

Format: **Decision**, then why, then the date.

---

## Decided

- **The brain runs at `effort: medium` by default,** set in `config.yaml` (`brain.effort`). *Why:* the spec puts accuracy first ("no made-up numbers"), and medium kept answers grounded in tool calls while staying near 3 s. Chunk 3 tunes it. *(2026-10-29)*
- **Model names in config map to exact IDs** (`sonnet` → `claude-sonnet-5-5`, `opus` → `claude-opus-5-5`, `haiku` → `claude-haiku-5-5`) in `jarvis/config.py`, so a CLI alias can't drift to a different model. "Think hard" and "deep dive" switch to the deep model for that one question only. *(2026-10-29)*
- **The brain session is isolated from this machine's Claude setup:** no `CLAUDE.md`, no user settings, no skills or subagents (`setting_sources=[]`, `tools=[]`), and prompts aren't expanded (`verbatim_prompts`). Verified at startup: the CLI lists exactly the 11 Jarvis MCP tools. *Why:* the read-only rule, and the dev `CLAUDE.md` in this repo is not Jarvis's personality. *(2026-10-29)*
- **Panel keys reach the brain through the post-tool hook,** as a short note added to each tool result ("Panels on screen: 'USDC peg' (say: usdc, usd coin)"). *Why:* the brain learns about a panel the moment it exists, and tools stay unaware of the HUD. *(2026-10-29)*
- **Peg rules:** only USD pegs are judged. `warn` at 0.5% off $1, `alert` at 2%. `list_peg_alerts` only reports coins with more than $1B supply, so tiny tokens don't cry wolf. When two coins share a symbol (there are several "USDS"), the bigger one wins. *(2026-10-29)*
- **Stock change is close to close (1 trading day); crypto change is rolling 24 hours.** Each result says which (`change_label`). Yahoo calls get a 10 s budget per request, then the symbol comes back as unavailable. *Why:* yfinance can hang. A missing number beats a stalled answer. *(2026-10-29)*
- **Saved notes:** theses go to `notes/theses.md` as a new `## heading` section with the date. Other thoughts go to `notes/inbox.md` as dated bullets. The brain chooses the heading. *(2026-10-29)*
- **In `--text` mode, `speech` events go out as each sentence is generated,** since there's no audio to sync to. Chunk 2 moves them to playback start. *(2026-10-29)*
- **`get_article_summary` only fetches public `http(s)` links,** never localhost or private addresses. *Why:* feed text could steer the model toward a local URL. *(2026-10-29)*
- **Logs go to `logs/jarvis.log`,** not the terminal, so the REPL stays readable. *(2026-10-29)*
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
6. **Brief length on busy days.** The first live briefs ran about 230 to 260 words, a bit over the spec's 60 to 90 seconds (roughly 150 to 220 words). Headlines take most of it. *Default:* tighten in Chunk 3 by keeping each headline to one sentence. Say if a longer brief on big news days is fine.
