import pytest

from jarvis.timing import TimingHud, TurnClock, build_record, first_word_target


class FakeTime:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class Sink:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log_timing(self, record):
        self.records.append(record)


def test_voice_turn_spans_count_from_when_you_stopped_talking():
    marks = {
        "wake": 0.0,
        "chime": 0.05,
        "speech_end": 2.0,
        "endpointed": 2.7,
        "transcribed": 3.0,
        "asked": 3.0,
        "tool_start": 4.2,
        "tool_end": 4.5,
        "first_text": 5.5,
        "first_audio": 5.1,
        "answer_audio": 5.8,
        "done": 9.0,
    }
    rec = build_record(marks, tool_calls=1)
    assert rec["source"] == "wake"
    assert rec["spans"] == {
        "wake_to_chime": 50,
        "endpoint": 700,
        "stt": 300,
        "brain_first_text": 2500,
        "tools": 300,
        "text_to_audio": 300,
        "first_audio": 3100,
        "answer_audio": 3800,
        "total": 7000,
    }


def test_typed_turn_counts_from_when_it_was_asked():
    rec = build_record({"asked": 1.0, "first_text": 2.0, "answer_audio": 2.1, "done": 3.0}, 0)
    assert rec["source"] == "typed"
    assert rec["spans"]["answer_audio"] == 1100
    assert "endpoint" not in rec["spans"]


@pytest.mark.parametrize(
    ("marks", "source"),
    [({"ptt": 0, "speech_end": 1}, "ptt"), ({"speech_end": 1}, "follow_up")],
)
def test_source(marks, source):
    assert build_record(marks, 0)["source"] == source


def test_first_word_target_depends_on_tool_calls():
    assert first_word_target(1) == 2500
    assert first_word_target(3) == 5000


def test_clock_keeps_first_mark_unless_latest_and_saves_on_finish():
    now, sink = FakeTime(), Sink()
    clock = TurnClock(sink, now)
    clock.mark("asked")
    now.t = 101.0
    clock.mark("asked")  # ignored: first one wins
    clock.mark("tool_end", latest=True)
    now.t = 101.5
    clock.mark("tool_end", latest=True)
    rec = clock.finish("q", "claude-sonnet-5-5")
    assert rec["spans"]["total"] == 1500
    assert sink.records == [rec]
    assert rec["question"] == "q" and rec["interrupted"] is False
    assert clock.finish() is None  # nothing in progress


def test_discard_drops_the_turn():
    clock = TurnClock(Sink(), FakeTime())
    clock.mark("wake")
    clock.discard()
    assert clock.finish() is None


class FakeHud:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def tool_call(self, tool, fn, args=None):
        self.events.append(("tool_call", tool, fn))

    async def card(self, **card):
        self.events.append(("card", card["title"]))

    async def speech(self, text):
        self.events.append(("speech", text))

    async def state(self, value):
        self.events.append(("state", value))


@pytest.mark.asyncio
async def test_timing_hud_marks_tools_and_audio_and_passes_everything_through():
    now = FakeTime()
    clock = TurnClock(None, now)
    hud = FakeHud()
    screen = TimingHud(hud, clock, filler="Checking.")
    clock.mark("asked")
    now.t = 101.0
    await screen.tool_call("pegs", "get_peg", {})
    await screen.tool_call("pegs", "get_supply_change", {})
    now.t = 101.4
    await screen.card(title="USDC peg")
    now.t = 101.6
    await screen.speech("Checking.")
    now.t = 102.0
    await screen.speech("USDC is at a dollar.")
    await screen.state("idle")  # not timed, still forwarded
    rec = clock.finish()
    assert rec["tool_calls"] == 2
    assert rec["spans"]["tools"] == 400
    assert rec["spans"]["first_audio"] == 1600  # the filler counts as the first word
    assert rec["spans"]["answer_audio"] == 2000
    assert [e[0] for e in hud.events] == [
        "tool_call",
        "tool_call",
        "card",
        "speech",
        "speech",
        "state",
    ]
