import asyncio
import io

import pytest

from jarvis.turns import FILLER, TurnRunner


class FakeHud:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def clear(self):
        self.events.append(("clear",))

    async def heard(self, text, final=True):
        self.events.append(("heard", text, final))

    async def state(self, value):
        self.events.append(("state", value))

    async def speech(self, text):
        self.events.append(("speech", text))


class FakeVoice:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, sentence):
        self.said.append(sentence)

    async def done(self):
        return None


class FakeBrain:
    """Streams `deltas`. With `tool_seconds`, a tool call (and the second round trip to
    Claude) takes that long before the text."""

    def __init__(self, deltas, tool_seconds=0.0) -> None:
        self.deltas = deltas
        self.tool_seconds = tool_seconds
        self._looked_up = False

    def looking_up(self) -> bool:
        return self._looked_up

    async def ask(self, text):
        if self.tool_seconds:
            self._looked_up = True
            await asyncio.sleep(self.tool_seconds)
        for d in self.deltas:
            yield d


REPLY = ["USDC is at ninety-nine ", "point nine eight cents. Supply ", "is flat."]


@pytest.mark.asyncio
async def test_captions_mode_sends_each_sentence_to_the_hud():
    hud = FakeHud()
    reply = await TurnRunner(FakeBrain(REPLY), hud, out=io.StringIO()).run("how's usdc?")
    assert reply == "".join(REPLY)
    assert hud.events == [
        ("clear",),
        ("heard", "how's usdc?", True),
        ("state", "thinking"),
        ("state", "speaking"),
        ("speech", "USDC is at ninety-nine point nine eight cents."),
        ("state", "speaking"),
        ("speech", "Supply is flat."),
        ("state", "idle"),
    ]


@pytest.mark.asyncio
async def test_sentences_go_to_the_voice():
    hud, voice = FakeHud(), FakeVoice()
    await TurnRunner(FakeBrain(REPLY), hud, voice, out=io.StringIO()).run("q")
    assert voice.said == ["USDC is at ninety-nine point nine eight cents.", "Supply is flat."]
    assert ("speech", "Supply is flat.") not in hud.events  # the voice sends those


@pytest.mark.asyncio
async def test_filler_as_soon_as_a_tool_starts():
    hud, voice = FakeHud(), FakeVoice()
    brain = FakeBrain(REPLY, tool_seconds=0.3)
    await TurnRunner(brain, hud, voice, out=io.StringIO(), filler=FILLER).run("q")
    assert voice.said[0] == FILLER
    assert voice.said[1:] == ["USDC is at ninety-nine point nine eight cents.", "Supply is flat."]


@pytest.mark.asyncio
async def test_no_filler_when_no_tool_is_used():
    voice = FakeVoice()
    await TurnRunner(FakeBrain(REPLY), FakeHud(), voice, out=io.StringIO(), filler=FILLER).run("q")
    assert FILLER not in voice.said
