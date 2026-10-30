from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.config import CONFIG_PATH, load_config


def test_repo_config_loads():
    cfg = load_config(CONFIG_PATH)
    assert cfg.brain.default_model == "sonnet"
    assert cfg.brief.move_threshold_pct == 3
    assert "USDC" in cfg.stt.vocab
    assert cfg.news_feeds
    assert cfg.debug is False


def test_note_paths_resolve_against_config_folder(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("watchlist_file: notes/w.md\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.watchlist_file == tmp_path / "notes" / "w.md"
    assert cfg.theses_file == tmp_path / "notes" / "theses.md"


def test_empty_file_gives_defaults(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.followup_window_seconds == 8
    assert cfg.tts.voice == "en_US-ryan-medium"


@pytest.mark.parametrize(
    "text",
    [
        "brain:\n  defualt_model: opus\n",  # typo
        "wake_word:\n  sensitivity: 2\n",  # out of range
    ],
)
def test_bad_config_is_rejected(tmp_path: Path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)
