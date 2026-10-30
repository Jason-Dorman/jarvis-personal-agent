import pytest

from jarvis.checks import (
    count_triggers,
    same_words,
    summarize_timings,
    vocab_cases,
    vocab_hit,
)


def rec(first_audio, tools=1, interrupted=False, **spans):
    return {
        "tool_calls": tools,
        "interrupted": interrupted,
        "spans": {"first_audio": first_audio, **spans},
    }


def test_summary_medians_and_targets():
    records = [
        rec(2000),  # met (1 tool, 2.5 s)
        rec(3000),  # missed
        rec(4500, tools=2),  # met (2 tools, 5 s)
        rec(1000, interrupted=True),  # not judged
        rec(2200, wake_to_chime=40),
    ]
    s = summarize_timings(records)
    first = next(r for r in s["rows"] if r["key"] == "first_audio")
    assert first["median"] == 2200 and first["n"] == 5 and first["p90"] == 4500
    assert (s["first_word_met"], s["first_word_judged"]) == (3, 4)
    assert (s["wake_met"], s["wake_judged"]) == (1, 1)


def test_every_vocab_word_gets_a_phrase_that_contains_it():
    cases = vocab_cases(["USDC", "T-bills", "Ondo"])
    assert ("What's the latest on Ondo?", "Ondo") in cases
    for phrase, word in cases:
        assert vocab_hit(phrase, word)


@pytest.mark.parametrize(
    ("heard", "word", "hit"),
    [
        ("Where are T bills yielding?", "T-bills", True),
        ("What moved on my watch list?", "watchlist", True),
        ("How's USD C holding up?", "USDC", True),
        ("That's tether.", "Tether", True),
        ("How's Husk holding up?", "USDC", False),
    ],
)
def test_vocab_hit_ignores_case_spaces_and_hyphens(heard, word, hit):
    assert vocab_hit(heard, word) is hit


def test_same_words():
    assert same_words("and tether", "And Tether?")
    assert not same_words("That's Tether.", "And Tether?")


def test_count_triggers_debounces_each_threshold():
    # 0.1 s frames: a wake (two high frames), quiet, then a weaker blip 3 s later
    scores = [0.0, 0.8, 0.9, 0.0] + [0.0] * 26 + [0.45]
    counts = count_triggers(scores, thresholds=(0.4, 0.5), frame_seconds=0.1, refractory=2.0)
    assert counts == {0.4: 2, 0.5: 1}
