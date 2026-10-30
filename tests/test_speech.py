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


# ---- speakable(): text cleanup before Piper ----
from jarvis.speech import speakable  # noqa: E402


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        # tickers Piper mangles as words ("USDC" came out as "Husk")
        ("USDC is at a dollar.", "U S D C is at a dollar."),
        ("BTC, ETH and SPY.", "B T C, E T H and S P Y."),
        # tickers people say as words stay words
        ("HYPE and COIN rallied.", "HYPE and COIN rallied."),
        # money
        ("Bitcoin is at $77,000.", "Bitcoin is at 77,000 dollars."),
        ("Supply rose $1.2B.", "Supply rose 1.2 billion dollars."),
        ("Volume hit $3.4M today.", "Volume hit 3.4 million dollars today."),
        ("Worth $20 billion.", "Worth 20 billion dollars."),
        ("USDT is at $0.9998.", "U S D T is at 99.98 cents."),
        ("Fees were $0.05.", "Fees were 5 cents."),
        ("Supply is 1.5T.", "Supply is 1.5 trillion."),
        # signed moves
        ("SPY fell -1.3% today.", "S P Y fell 1.3% today."),
        ("ETH +4.2% on the day.", "E T H up 4.2% on the day."),
        ("Gold -0.8% this week.", "Gold down 0.8% this week."),
        # units and shorthand
        ("Over the last 24h.", "Over the last 24 hours."),
        ("Supply is flat over 7d.", "Supply is flat over 7 days."),
        ("The 10Y yield.", "The 10 year yield."),
        ("Spreads widened 15 bps.", "Spreads widened 15 basis points."),
        ("Up 1 bp.", "Up 1 basis point."),
        ("RWA and T-bills.", "R W A and T bills."),
        ("BTC vs. ETH & SOL.", "B T C versus E T H and SOL."),
        ("~3% higher.", "about 3% higher."),
        # markdown and links never reach the voice
        ("**USDC** is _fine_.", "U S D C is fine."),
        ("- Pegs are normal", "Pegs are normal"),
        ("## Markets", "Markets"),
        ("See [the post](https://x.com/a).", "See the post."),
        ("Read https://example.com/a now.", "Read the link now."),
        # already spoken numbers are left alone
        ("ninety-nine point nine eight cents", "ninety-nine point nine eight cents"),
    ],
)
def test_speakable(text, spoken):
    assert speakable(text) == spoken


def test_speakable_drops_emoji_and_extra_spaces():
    assert speakable("Pegs look good 🚀  today.") == "Pegs look good today."
