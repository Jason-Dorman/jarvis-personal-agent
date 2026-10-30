# Jarvis — System Spec (v1)

A personal finance and fintech assistant. It runs on your machine. You talk to it, and it talks back.

---

## 1. What it is

Jarvis is a voice assistant for markets, crypto, and fintech.

You say "Hey Jarvis." You ask a question. It checks real data with its tools. It answers out loud in a few sentences.

Ask for your morning brief and it gives you a short spoken rundown of markets, pegs, news, and your theses. It never runs on its own.

## 2. Goals

- Talk to it hands-free, like a person.
- Answer with real data, not the model's memory.
- Keep audio on your machine.
- Start small. Add one tool at a time.

## 3. Non-goals (v1)

- No trading. No sending messages. No moving money.
- No phone app. Desktop only.
- No custom voice or custom wake word.
- No vector database.

---

## 4. How you use it

**Ask a question**
> You: "Hey Jarvis, how's USDC holding up?"
> Jarvis: "USDC is at 99.98 cents. Supply is up 1.2% this week. No peg stress."

**Follow up without the wake word**
> You: "And Tether?"
> Jarvis: "USDT is at 1.0001. Supply is flat."

For 8 seconds after Jarvis finishes, it keeps listening. You can follow up without saying "Hey Jarvis" again.

**Interrupt it**
Start talking while Jarvis is speaking. It stops and listens.

**Get the morning brief**
> You: "Hey Jarvis, give me my morning brief."
> Jarvis: *(speaks a 60–90 second brief and saves the text)*

The brief only runs when you ask for it. There is no schedule. You can also type the request in the HUD.

**Save a thought**
> You: "Jarvis, note that I think tokenized treasuries hit $20B by year end."
> Jarvis: "Saved to your theses."

**Push-to-talk backup**
Hold a hotkey to talk. Use it when the room is noisy or the wake word misfires.

---

## 5. Architecture

```
 Mic
  │
  ▼
[Wake word] ──► [Voice detection] ──► [Speech-to-text]
 openWakeWord     Silero VAD           faster-whisper
                                            │  (text only)
                                            ▼
                                     [Brain: Claude]
                                     Claude Agent SDK
                                       │        ▲
                              tool calls│        │results
                                       ▼        │
                       ┌────────────────────────────────┐
                       │ Tools (MCP servers)            │
                       │ markets · pegs · news · notes  │
                       └────────────────────────────────┘
                                            │
                                            ▼
                                   [Text-to-speech] ──► Speaker
                                        Piper

 [Brief]     ── runs only when you ask for it
 [Memory]    ── SQLite + notes/ folder
```

All audio stays local. Only the transcribed text goes to Claude.

---

## 6. Components

### 6.1 Wake word
- **Tool:** openWakeWord. It ships a ready-made "hey jarvis" model.
- Runs all the time on CPU. Very light.
- Tune the sensitivity in `config.yaml`.

### 6.2 Voice detection
- **Tool:** Silero VAD.
- Tells the system when you start and stop talking.
- Stop after 0.7 seconds of silence.

### 6.3 Speech-to-text
- **Tool:** faster-whisper (Linux/Windows) or whisper.cpp / mlx-whisper (Apple Silicon).
- Model: `small.en` to start. Move up to `medium.en` if accuracy is weak.
- Add a custom vocabulary list so it hears finance words right: USDC, USDT, perps, RWA, Hyperliquid, basis, T-bills, etc.

### 6.4 Brain
- **Tool:** Claude Agent SDK, Python.
- Default model: Claude Sonnet (`claude-sonnet-5-5`). Use Claude Opus (`claude-opus-5-5`) only when you say "think hard" or "deep dive."
- Keeps the conversation history for the current session.
- **Voice rules in the system prompt:**
  - Answer in 1–3 short sentences unless asked for more.
  - Lead with the answer.
  - Say numbers the way people speak them ("ninety-nine point nine eight cents").
  - No lists, tables, or markdown in spoken answers.
  - Every number must come from a tool call. If no tool has it, say so.

### 6.5 Text-to-speech
- **Tool:** Piper. Runs locally and fast.
- Speak sentence by sentence as the answer streams in. Don't wait for the full reply.
- Pick one voice and keep it.

### 6.6 Barge-in
- While Jarvis speaks, VAD keeps listening.
- If you talk, stop playback right away and start a new turn.
- Use echo cancellation (or headphones) so Jarvis doesn't hear itself.

### 6.7 Morning brief trigger
- **On demand only.** No cron, no launchd, no timer.
- Triggered when you ask for it in any way Jarvis takes input: voice ("give me my morning brief", "run the brief"), typed in the HUD, or typed in the text REPL.
- The brain recognizes the request and calls `brief.get_brief_data()` (section 7). That one call gathers everything the brief needs, so the brief starts fast.
- For testing without audio: `python -m jarvis.brief` prints the brief text and saves it.

### 6.8 Memory
- **SQLite (`jarvis.db`):** price snapshots, tool call log, conversation log.
- **`notes/` folder (markdown):** your watchlist, theses, and saved thoughts. You can edit these by hand.
- Jarvis reads `watchlist.md` and `theses.md` at the start of each session.

---

## 7. Tools (v1)

Each tool is its own MCP server. Each one does one thing.

| Tool | Functions | Source |
|---|---|---|
| `markets` | `get_price(symbol)`, `get_movers()`, `get_watchlist_snapshot()` | CoinGecko (crypto), yfinance (stocks, ETFs, rates) |
| `pegs` | `get_peg(stablecoin)`, `get_supply_change(stablecoin, days)`, `list_peg_alerts()` | DefiLlama stablecoins API |
| `news` | `get_headlines(topic, hours)`, `get_article_summary(url)` | RSS feeds listed in `config.yaml` |
| `notes` | `read_notes(file)`, `append_note(file, text)` | Local `notes/` folder |
| `brief` | `get_brief_data()` | Gathers watchlist movers, pegs, headlines, and theses in one call by reusing the other tools' code |

Every tool call is logged with time, inputs, and outputs.

---

## 8. The morning brief

Runs only when you ask (see 6.7). Spoken in this order. Target 60–90 seconds. The brief is the one answer allowed to run past the 1–3 sentence rule.

1. One-line market mood.
2. Your watchlist: anything that moved more than your threshold.
3. Stablecoin pegs: any stress, or "all pegs normal."
4. Top 3 fintech or digital-asset headlines.
5. Anything that touches one of your theses.

The full text is saved to `briefs/YYYY-MM-DD.md`. Asking again on the same day replaces that file.

---

## 9. Safety rules

- **Read-only.** No tool can trade, transfer, or send messages.
- **No made-up numbers.** If a tool fails, Jarvis says the data is unavailable.
- **Secrets** live in `.env`. Never in code or notes.
- **Audio** is never saved to disk unless you turn on debug mode.
- **Logs** stay local.

---

## 10. Performance targets

| Step | Target |
|---|---|
| Wake word to "listening" sound | under 0.3 s |
| You stop talking to first spoken word | under 2.5 s (simple question) |
| Same, with 2–3 tool calls | under 5 s |
| Wake word false triggers | under 1 per hour of normal room noise |
| Speech-to-text accuracy on finance words | 95%+ on your vocab list |

If a tool call will take long, Jarvis says "Checking" first so the silence isn't dead.

---

## 11. Config (`config.yaml`)

```yaml
wake_word:
  model: hey_jarvis
  sensitivity: 0.5
followup_window_seconds: 8
push_to_talk_hotkey: "ctrl+space"

stt:
  model: small.en
  vocab: [USDC, USDT, perps, RWA, Hyperliquid, basis, T-bills, stablecoin]

brain:
  default_model: sonnet
  deep_model: opus
  max_spoken_sentences: 3

tts:
  voice: en_US-ryan-medium

brief:
  move_threshold_pct: 3     # watchlist moves smaller than this are skipped

watchlist_file: notes/watchlist.md
theses_file: notes/theses.md
news_feeds:
  - https://www.coindesk.com/arc/outboundfeeds/rss/
  - https://www.theblock.co/rss.xml
  - https://www.federalreserve.gov/feeds/press_all.xml

debug: false                # true lets audio touch disk for troubleshooting
```

Unknown keys and out-of-range values are rejected at startup. Relative file paths resolve against the folder `config.yaml` is in.

---

## 12. Folder layout

```
jarvis/
├── pyproject.toml
├── .env                    # API key, never committed
├── .env.example            # template for .env
├── config.yaml
├── jarvis/
│   ├── config.py           # typed settings from config.yaml
│   ├── main.py             # starts the voice loop
│   ├── voice_in.py         # wake word, VAD, speech-to-text
│   ├── voice_out.py        # Piper, streaming, barge-in
│   ├── agent.py            # Claude + tool wiring + voice rules
│   ├── brief.py            # brief data + save; `python -m jarvis.brief` for text-only
│   └── memory.py           # SQLite read/write
├── tools/
│   ├── markets_server.py
│   ├── pegs_server.py
│   ├── news_server.py
│   ├── notes_server.py
│   └── brief_server.py
├── hud/                    # see section 15.7
├── tests/
├── notes/
│   ├── watchlist.md
│   └── theses.md
├── briefs/
├── logs/
└── jarvis.db
```

---

## 13. Build phases

The build plan lives in [BUILD-PLAN.md](BUILD-PLAN.md). It is the source of truth for order and status.

**Done when:** you can ask five real questions in a row, by voice, without touching the keyboard, and every number checks out.

---

## 14. After v1

- Plug in your trading platform as a read-only tool.
- Optional scheduled brief (off by default), if on-demand ever feels like a chore.
- Alerts: Jarvis speaks up on its own for peg breaks or big moves.
- Local model (Ollama) for private questions about your own positions.
- Phone access through a small private web app.

---

## 15. The HUD (screen)

A browser screen at `http://localhost:8765`. As Jarvis talks, it shows the same data on screen.

### 15.1 What you see

- **Core (center):** a glowing ring that reacts to Jarvis's voice. It spins fast and turns amber while Jarvis works.
- **Tool markers:** each tool call orbits the core with its name (for example `pegs.get_peg`) until its data comes back.
- **Panels:** each tool result becomes a panel. A line connects it to the core.
- **Spoken sync:** panels wait as small squares inside the core. Each one flies out when Jarvis says its key word. If Jarvis mentions a panel already on screen, that panel lights up.
- **Captions:** the sentence being spoken types out at the bottom. The one before it fades above.
- **Log (left):** what you asked and what Jarvis said.
- **Ticker (bottom):** your watchlist.
- **Input:** type a question, or hold Space (or the button) to talk.

Without the backend, the page runs a labeled demo so you can see it work.

### 15.2 How it connects

```
voice_in ──► agent ──► voice_out
    │          │           │
    └──────────┴─── hud.emit(...) ──► server.py ──WebSocket──► browser
                                          ▲
              typed questions, push-to-talk ┘
```

`server.py` serves the page and holds one `hud` object. Every part of Jarvis calls `hud.<method>()`. The browser only draws what it's told.

### 15.3 Events (backend → screen)

| Event | Fields | When to send |
|---|---|---|
| `state` | `value`: idle, listening, thinking, speaking | Every state change |
| `transcript` | `role`: "user", `text`, `final` | As speech-to-text produces words |
| `tool_call` | `tool`, `fn`, `args` | Right before a tool runs |
| `tool_result` | `tool` | Tool finished with no panel |
| `card` | see 15.4 | Right after a tool returns |
| `speech` | `text` | The moment each sentence's audio starts |
| `level` | `value` 0–1 | About 20 times a second while speaking |
| `ticker` | `items`: `[{symbol, price, delta}]` | On watchlist refresh |
| `clear` | none | Start of each new question |

### 15.4 Panel kinds

Every card has `kind`, `title`, and optional `id`, `tool`, `source`, `status` (`warn`, `alert`), and `keys`.

| Kind | Extra fields | Use |
|---|---|---|
| `metric` | `value`, `decimals`, `unit`, `delta`, `delta_label`, `spark`, `band` | One number, like a peg price |
| `chart` | `series`, `band`, `decimals` | A price history |
| `metrics` | `rows`: `[{label, value, delta}]` | Watchlist movers |
| `headlines` | `items`: `[{title, source, age}]` | News |
| `note` | `text` | Confirmations |
| `alert` | `text` | Peg break, big move. Shows right away. |

**`keys`** are the words that make a panel appear when Jarvis says them. Example: `["usdc"]`. If you leave them out, the screen uses any all-caps word in the title. Panels nobody mentions appear when Jarvis finishes talking. Send `"reveal": "now"` to skip the wait.

### 15.5 Events (screen → backend)

| Event | Fields |
|---|---|
| `text` | `text`: a typed question |
| `ptt` | `down`: true when held, false when released |

The agent reads these from `hud.inbox`.

### 15.6 Wiring rules

- Wrap every tool call: `tool_call` before, `card` after. Build the card from the tool result, never from the model's words.
- Tell the brain which panels are on screen and their keys. Ask it to name each one when it speaks about it.
- `voice_out` sends `speech` when a sentence starts playing, not when it's generated. That keeps the captions in sync with the voice.
- `voice_out` sends `level` from the playback audio's loudness.

### 15.7 Files

```
hud/
├── index.html   # the screen, one file
├── server.py    # serves it, WebSocket bridge, hud object
└── README.md    # how to run and wire it
```
