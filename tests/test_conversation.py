"""The voice loop's barge-in and turn taking, with fake ears, mouth, and brain."""

import asyncio
import io

import numpy as np
import pytest

from jarvis.conversation import Inputs, VoiceLoop
from jarvis.turns import TurnRunner
from jarvis.voice_in import BargeIn

PCM = np.ones(1600, np.int16)


class FakeHud:
    def __init__(self) -> None:
        self.states: list[str] = []

    async def clear(self): ...
    async def heard(self, text, final=True): ...
    async def speech(self, text): ...

    async def state(self, value):
        self.states.append(value)


class FakeMouth:
    def __init__(self) -> None:
        self.chimes = 0
        self.stopped = 0
        self.said: list[str] = []
        self.playing = False

    async def chime(self):
        self.chimes += 1

    def stop(self):
        self.stopped += 1

    def recent_speech(self):
        return " ".join(self.said[-3:])

    async def say(self, sentence):
        self.said.append(sentence)

    async def done(self): ...


class SlowBrain:
    """Streams one word every `gap` seconds."""

    model = "claude-sonnet-5-5"

    def __init__(self, words: int = 40, gap: float = 0.02) -> None:
        self.words, self.gap = words, gap
        self.interrupted = 0

    def looking_up(self):
        return False

    async def ask(self, text):
        for _ in range(self.words):
            await asyncio.sleep(self.gap)
            yield "Word. "

    async def interrupt(self):
        self.interrupted += 1


class FakeEars:
    """`barge` is what watch_for_barge_in returns after `barge_after` s (None: never)."""

    def __init__(self, barge=None, barge_after=0.1, heard="And Tether?", capture=PCM):
        self.barge, self.barge_after = barge, barge_after
        self.heard, self._capture = heard, capture
        self.last_speech_at = None
        self.captures: list[dict] = []

    def reset(self): ...

    async def wait_for_wake(self):
        await asyncio.Event().wait()

    async def watch_for_barge_in(self, by_voice, threshold, jarvis_talking):
        if self.barge is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.barge_after)
        return self.barge

    async def capture(self, listen, on_partial=None, after_wake=False, lead=()):
        self.captures.append({"after_wake": after_wake, "lead": list(lead)})
        return self._capture

    async def record(self, until, on_partial=None):
        await until.wait()
        return PCM

    async def transcribe(self, pcm, quick=False):
        return self.heard


def make_loop(ears, brain, inputs=None, mode="wake_word"):
    mouth, hud = FakeMouth(), FakeHud()
    runner = TurnRunner(brain, hud, mouth, out=io.StringIO())
    loop = VoiceLoop(
        ears,
        mouth,
        runner,
        hud,
        inputs or Inputs(),
        silence=0.7,
        vad_threshold=0.5,
        followup_seconds=8,
        barge_in=mode,
    )
    return loop, mouth


async def test_wake_word_barge_in_cuts_the_answer_and_takes_the_next_question():
    brain = SlowBrain()
    loop, mouth = make_loop(FakeEars(barge=BargeIn("wake")), brain)
    nxt = await asyncio.wait_for(loop._answer("How's USDC?"), 2)
    assert nxt == "And Tether?"
    assert mouth.stopped == 1 and brain.interrupted == 1
    assert mouth.chimes == 1  # a wake-word barge-in chimes like any wake
    assert len(mouth.said) < brain.words  # the answer was cut short
    assert loop.ears.captures == [{"after_wake": True, "lead": []}]


async def test_voice_barge_in_keeps_what_you_already_said():
    lead = [PCM, PCM]
    ears = FakeEars(barge=BargeIn("voice", lead))
    loop, mouth = make_loop(ears, SlowBrain(), mode="voice")
    assert await asyncio.wait_for(loop._answer("q"), 2) == "And Tether?"
    assert mouth.chimes == 0  # you're already talking
    assert len(ears.captures[0]["lead"]) == 2


async def test_push_to_talk_during_an_answer_barges_in():
    inputs = Inputs()
    brain = SlowBrain()
    loop, mouth = make_loop(FakeEars(), brain, inputs)
    task = asyncio.create_task(loop._answer("q"))
    await asyncio.sleep(0.1)
    inputs.put("ptt", True)
    await asyncio.sleep(0.05)
    inputs.put("ptt", False)
    assert await asyncio.wait_for(task, 2) == "And Tether?"
    assert brain.interrupted == 1 and mouth.stopped == 1


async def test_typed_question_during_an_answer_waits_its_turn():
    inputs = Inputs()
    ears = FakeEars(capture=None)  # nobody follows up by voice
    loop, mouth = make_loop(ears, SlowBrain(words=5), inputs)
    task = asyncio.create_task(loop._answer("q"))
    await asyncio.sleep(0.03)
    inputs.put("text", "and tether?")
    await asyncio.wait_for(task, 2)
    assert mouth.stopped == 0  # not a barge-in
    assert len(mouth.said) == 5  # the whole answer was spoken
    assert await inputs.get() == ("text", "and tether?")  # still queued, not lost


async def test_a_finished_turn_leaves_no_listener_behind():
    """The barge-in watcher must stop with the turn, or it steals mic frames and inputs."""
    watching = []

    class Ears(FakeEars):
        async def watch_for_barge_in(self, by_voice, threshold, jarvis_talking):
            watching.append(True)
            try:
                await asyncio.Event().wait()
            finally:
                watching.pop()

    inputs = Inputs()
    loop, _ = make_loop(Ears(capture=None), SlowBrain(words=3), inputs)
    await asyncio.wait_for(loop._answer("q"), 2)
    await asyncio.sleep(0)
    assert watching == []
    inputs.put("text", "next question")
    assert await asyncio.wait_for(inputs.get(), 1) == ("text", "next question")


async def test_no_barge_in_goes_to_the_follow_up_window():
    loop, mouth = make_loop(FakeEars(capture=None), SlowBrain(words=3))
    assert await asyncio.wait_for(loop._answer("q"), 2) is None
    assert mouth.stopped == 0 and len(mouth.said) == 3


async def test_barge_in_off_never_listens_during_the_answer():
    loop, mouth = make_loop(FakeEars(barge=BargeIn("wake"), capture=None), SlowBrain(5), mode="off")
    assert await asyncio.wait_for(loop._answer("q"), 2) is None
    assert mouth.stopped == 0


@pytest.mark.parametrize("mode", ["wake_word", "voice"])
async def test_barge_in_mode_is_passed_to_the_ears(mode):
    seen = {}

    class Ears(FakeEars):
        async def watch_for_barge_in(self, by_voice, threshold, jarvis_talking):
            seen["by_voice"] = by_voice
            return await super().watch_for_barge_in(by_voice, threshold, jarvis_talking)

    loop, _ = make_loop(Ears(capture=None), SlowBrain(words=2), mode=mode)
    await asyncio.wait_for(loop._answer("q"), 2)
    assert seen["by_voice"] is (mode == "voice")


async def test_voice_barge_in_that_hears_jarvis_itself_switches_to_wake_word():
    class Mouth(FakeMouth):
        def recent_speech(self):
            return "Two names moved on your watchlist, past your three percent threshold."

    ears = FakeEars(barge=BargeIn("voice", [PCM]), heard="Two needs moved on.")
    loop, _ = make_loop(ears, SlowBrain(), mode="voice")
    loop.mouth = Mouth()
    assert await asyncio.wait_for(loop._answer("q"), 2) is None  # echo dropped
    assert loop.barge_in == "wake_word"
