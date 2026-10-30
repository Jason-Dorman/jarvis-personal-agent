# HUD

The screen for Jarvis. It shows what Jarvis is saying, as it says it.

## See it now

Open `index.html` in a browser. With no backend running, it plays a demo. Everything on screen is labeled demo data.

Type a question or hold Space to run another demo turn.

## Run it on localhost

From the repo root, after `uv sync`:

```bash
uv run python hud/server.py --demo
```

Open http://localhost:8765. The dot in the top right turns green when the page is linked. Type anything. The bridge answers with a short test turn.

For the real Jarvis, run `uv run python -m jarvis.main --text` from the repo root instead. It serves the same page on the same port.

## Wire it to Jarvis

Import the `hud` object from `hud/server.py` (run Jarvis from the repo root so `hud` is importable). It's called from three places.

**1. Around every tool call (done: `jarvis/hooks.py`)**

The Agent SDK's pre/post tool hooks send `tool_call` before each tool runs and a card (or `tool_result`) after. Cards are built in `jarvis/cards.py` from the tool's own result, never from the model's words. The tools themselves don't know the HUD exists. The post hook also tells the brain which panels just went on screen and their key words.

```python
# jarvis/hooks.py, simplified
async def pre(self, data, tool_use_id, ctx):
    tool, fn = split_mcp_name(data["tool_name"])  # "mcp__pegs__get_peg" → ("pegs", "get_peg")
    await hud.tool_call(tool, fn, data["tool_input"])
    return {}


async def post(self, data, tool_use_id, ctx):
    tool, fn = split_mcp_name(data["tool_name"])
    result = parse_tool_response(data["tool_response"])
    cards = cards_for(tool, fn, result)  # [] for errors
    for card in cards:
        await hud.card(**card)
    if not cards:
        await hud.tool_result(tool)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": panel_hint(cards),
        }
    }
```

The ticker is pushed at startup and every 5 minutes (`jarvis/main.py`). The server keeps the last one and sends it to any screen that connects later.

**2. In speech-to-text (voice_in, Chunk 2)**

```python
await hud.state("listening")
await hud.heard(partial_text, final=False)  # as words arrive
await hud.heard(full_text, final=True)  # when you stop talking
await hud.state("thinking")
```

**3. In playback (voice_out, Chunk 2)**

Until then, `jarvis/turns.py` sends `speech` as each sentence is generated.

```python
await hud.state("speaking")
for sentence, audio in sentences:
    await hud.speech(sentence)  # when this sentence starts playing
    for chunk in audio:
        play(chunk)
        await hud.level(rms(chunk))  # about 20x per second
await hud.state("idle")
```

**Typed questions and push-to-talk** arrive on `hud.inbox` as `{"type": "text", "text": ...}` and `{"type": "ptt", "down": true|false}`.

## Make panels appear on cue

Each card has `keys`. When Jarvis says one of those words, the panel flies out. Add this to the brain's system prompt:

> Panels on screen are listed with their key words. When you talk about one, say its key word.

The full event list is in section 15 of [`docs/jarvis-spec.md`](../docs/jarvis-spec.md).
