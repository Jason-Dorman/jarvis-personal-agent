import pytest

from jarvis.hotkey import parse_hotkey


@pytest.mark.parametrize(
    ("combo", "parsed"),
    [
        ("ctrl+alt+space", (0x3, 0x20)),
        ("Ctrl + Shift + K", (0x6, ord("K"))),
        ("f9", (0, 0x78)),
        ("win+1", (0x8, ord("1"))),
    ],
)
def test_parse_hotkey(combo, parsed):
    assert parse_hotkey(combo) == parsed


@pytest.mark.parametrize("combo", ["", "ctrl+hyper+space", "ctrl+alt+spacebar", "f25"])
def test_bad_hotkeys_are_rejected(combo):
    with pytest.raises(ValueError):
        parse_hotkey(combo)
