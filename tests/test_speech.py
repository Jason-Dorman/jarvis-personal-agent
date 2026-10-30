import pytest

from jarvis.speech import SentenceSplitter


def split_streamed(text: str, step: int = 3) -> list[str]:
    s = SentenceSplitter()
    out = []
    for i in range(0, len(text), step):
        out += s.feed(text[i : i + step])
    return out + s.flush()


def test_splits_sentences_as_they_arrive():
    s = SentenceSplitter()
    assert s.feed("USDC is at a dollar. Sup") == ["USDC is at a dollar."]
    assert s.feed("ply is up") == []
    assert s.flush() == ["Supply is up"]


def test_waits_for_the_next_word_before_cutting():
    s = SentenceSplitter()
    assert s.feed("Done.") == []
    assert s.feed(" ") == []
    assert s.feed("Next") == ["Done."]


@pytest.mark.parametrize("step", [1, 2, 5, 50])
def test_decimals_and_abbreviations_stay_whole(step):
    text = (
        "USDC is at 99.98 cents vs. a dollar. The U.S. Treasury yield is 4.99 percent. "
        "Dr. Powell spoke at 2 p.m. today! Is that new? Yes."
    )
    assert split_streamed(text, step) == [
        "USDC is at 99.98 cents vs. a dollar.",
        "The U.S. Treasury yield is 4.99 percent.",
        "Dr. Powell spoke at 2 p.m. today!",
        "Is that new?",
        "Yes.",
    ]


def test_lowercase_after_period_does_not_split():
    assert split_streamed("It rose 3 pct. on the day. Then fell.") == [
        "It rose 3 pct. on the day.",
        "Then fell.",
    ]


def test_newlines_end_a_chunk():
    assert split_streamed("Crypto is down\n\nOn pegs, all normal. Done.") == [
        "Crypto is down",
        "On pegs, all normal.",
        "Done.",
    ]


def test_quotes_after_punctuation():
    assert split_streamed('He said "hold." Then left.') == ['He said "hold."', "Then left."]


def test_flush_empty():
    assert SentenceSplitter().flush() == []
