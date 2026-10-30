import pytest

from jarvis.voice_in import Endpointer, Heard, clean_transcript, vocab_prompt


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
