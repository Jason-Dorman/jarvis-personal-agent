import numpy as np
import pytest

from jarvis.voice_in import (
    Endpointer,
    Heard,
    TalkOver,
    clean_transcript,
    normalize,
    sounds_like_echo,
    vocab_prompt,
)


def run(ends: Endpointer, pattern: str) -> list[Heard]:
    """'s' = speech frame, '.' = quiet frame."""
    return [ends.feed(c == "s") for c in pattern]


def ender(**kw) -> Endpointer:
    # 0.1 s frames keep the arithmetic readable
    return Endpointer(**{"start_timeout": 1.0, "silence": 0.3, "frame_seconds": 0.1, **kw})


def test_stops_after_enough_quiet():
    ends = ender()
    states = run(ends, "..sss..")
    assert states[-1] is Heard.SPEAKING
    assert ends.feed(False) is Heard.DONE
    assert ends.start_frame == 2


def test_short_pauses_mid_sentence_keep_listening():
    ends = ender()
    assert run(ends, "sss..sss..")[-1] is Heard.SPEAKING
    assert ends.feed(False) is Heard.DONE


def test_times_out_when_nobody_speaks():
    states = run(ender(), "." * 10)
    assert states[:9] == [Heard.WAITING] * 9
    assert states[9] is Heard.TIMEOUT


def test_once_speaking_the_start_timeout_no_longer_applies():
    ends = ender(start_timeout=0.3)
    assert run(ends, "..ssssssss")[-1] is Heard.SPEAKING


def test_a_short_blip_is_noise_not_speech():
    ends = ender(min_speech=0.3)
    assert run(ends, "s...")[-1] is Heard.WAITING
    assert ends.start_frame is None
    assert run(ends, "sss...")[-1] is Heard.DONE
    assert ends.start_frame == 4


def test_long_speech_is_cut_at_max_seconds():
    ends = ender(max_seconds=1.0)
    assert run(ends, "s" * 10)[-1] is Heard.DONE


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Hey Jarvis, how's USDC holding up?", "How's USDC holding up?"),
        ("hey jarvis how's USDC", "How's USDC"),
        ("Jarvis, note that rates are high.", "Note that rates are high."),
        ("...Jarvis. What moved?", "What moved?"),
        ("Okay Jarvis give me my morning brief", "Give me my morning brief"),
        ("and Tether?", "And Tether?"),
        ("How is Jarvis built?", "How is Jarvis built?"),  # only a leading wake phrase
        ("you", ""),
        (" Thank you. ", ""),
        ("Thanks for watching!", ""),
        ("Hey Jarvis.", ""),
        ("", ""),
    ],
)
def test_clean_transcript(raw, clean):
    assert clean_transcript(raw) == clean


def test_vocab_prompt():
    assert vocab_prompt(["USDC", "perps"]) == "Markets and crypto talk: USDC, perps."
    assert vocab_prompt([]) is None


def test_talking_over_jarvis_needs_steady_speech():
    over = TalkOver(min_speech=0.3, frame_seconds=0.1)
    assert [over.feed(True, True) for _ in range(3)] == [False, False, True]


def test_a_pause_resets_the_talk_over_count():
    over = TalkOver(min_speech=0.3, frame_seconds=0.1)
    for speech in (True, True, False, True, True):
        assert over.feed(speech, True) is False
    assert over.feed(True, True) is True


def test_speech_while_jarvis_is_quiet_is_not_barge_in():
    over = TalkOver(min_speech=0.3, frame_seconds=0.1)
    assert not any(over.feed(True, False) for _ in range(10))


def test_normalize_brings_quiet_speech_up_to_a_steady_peak():
    quiet = np.array([0.0, 0.03, -0.015], np.float32)
    assert np.isclose(np.max(np.abs(normalize(quiet))), 0.5)


def test_normalize_caps_the_gain_so_noise_stays_quiet():
    hiss = np.array([0.001, -0.001], np.float32)
    assert np.isclose(np.max(np.abs(normalize(hiss))), 0.02)
    assert normalize(np.zeros(4, np.float32)).tolist() == [0, 0, 0, 0]


@pytest.mark.parametrize(
    ("heard", "said", "echo"),
    [
        # From a speaker test with barge_in: voice. The mic heard Jarvis, not a person.
        ("Two needs moved on.", "Two names moved on your watchlist, past your threshold.", True),
        ("I think you're saying...", "I think you're saying my last answer came out", True),
        # Real follow-ups that reuse Jarvis's words are too short to judge
        ("What about Ethereum?", "Ethereum fell three point one percent.", False),
        ("And Tether?", "Tether is holding its peg.", False),
        ("Why did Hyperliquid jump so much today?", "Ethereum fell three percent.", False),
    ],
)
def test_sounds_like_echo(heard, said, echo):
    assert sounds_like_echo(heard, said) is echo
