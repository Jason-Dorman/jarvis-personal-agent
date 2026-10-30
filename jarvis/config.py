"""Typed settings from config.yaml (spec section 11).

Load once with `get_config()`. Unknown keys are rejected so a typo fails loudly
instead of silently falling back to a default.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

# Where runtime data lives (spec section 12). Fixed, not user settings.
DB_PATH = ROOT / "jarvis.db"
NOTES_DIR = ROOT / "notes"
BRIEFS_DIR = ROOT / "briefs"
LOGS_DIR = ROOT / "logs"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WakeWord(_Section):
    model: str = "hey_jarvis"
    sensitivity: float = Field(0.5, ge=0, le=1)


class Stt(_Section):
    model: str = "small.en"
    vocab: list[str] = []


class Brain(_Section):
    default_model: str = "sonnet"
    deep_model: str = "opus"
    max_spoken_sentences: int = Field(3, ge=1)


class Tts(_Section):
    voice: str = "en_US-ryan-medium"


class Brief(_Section):
    move_threshold_pct: float = Field(3, ge=0)


class Config(_Section):
    wake_word: WakeWord = WakeWord()
    followup_window_seconds: float = Field(8, ge=0)
    push_to_talk_hotkey: str | None = None
    stt: Stt = Stt()
    brain: Brain = Brain()
    tts: Tts = Tts()
    brief: Brief = Brief()
    watchlist_file: Path = Path("notes/watchlist.md")
    theses_file: Path = Path("notes/theses.md")
    news_feeds: list[str] = []
    debug: bool = False


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Read a config file. Relative file paths resolve against the file's folder."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(raw)
    base = path.resolve().parent
    return cfg.model_copy(
        update={
            "watchlist_file": base / cfg.watchlist_file,
            "theses_file": base / cfg.theses_file,
        }
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
