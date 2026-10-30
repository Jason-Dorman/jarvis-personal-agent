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

Drop `--demo` once your agent drives it.

## Wire it to Jarvis

Import the `hud` object from `hud/server.py` (run Jarvis from the repo root so `hud` is importable). Call it from three places.

**1. Around every tool call (agent)**

```python
from hud.server import hud


async def run_tool(tool, fn, args):
    await hud.tool_call(tool, fn, args)
    result = await call_mcp(tool, fn, args)  # your MCP client
    card = card_for(tool, fn, result)  # build from data, not model text
    if card:
        await hud.card(**card)
    else:
        await hud.tool_result(tool)
    return result


def card_for(tool, fn, r):
    if (tool, fn) == ("pegs", "get_peg"):
        return dict(
            id=f"peg-{r['symbol']}",
            tool=tool,
            kind="metric",
            title=f"{r['symbol']} peg",
            keys=[r["symbol"].lower()],
            value=r["price"],
            decimals=4,
            unit="USD",
            spark=r.get("history"),
            source="DefiLlama",
            status="warn" if abs(r["price"] - 1) > 0.003 else None,
        )
    return None
```

**2. In speech-to-text (voice_in)**

```python
await hud.state("listening")
await hud.heard(partial_text, final=False)  # as words arrive
await hud.heard(full_text, final=True)  # when you stop talking
await hud.state("thinking")
```

**3. In playback (voice_out)**

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
