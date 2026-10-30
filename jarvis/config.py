"""Typed settings from config.yaml (spec section 11).

Load once with `get_config()`. Unknown keys are rejected so a typo fails loudly
instead of silently falling back to a default.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

# Where runtime data lives (spec section 12). Fixed, not user settings.
DB_PATH = ROOT / "jarvis.db"
NOTES_DIR = ROOT / "notes"
BRIEFS_DIR = ROOT / "briefs"
LOGS_DIR = ROOT / "logs"
MODELS_DIR = ROOT / "models"  # downloaded on first run, gitignored


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WakeWord(_Section):
    model: str = "hey_jarvis"
    sensitivity: float = Field(0.5, ge=0, le=1)  # higher wakes more easily


class Audio(_Section):
    input_device: str | None = None  # None = Windows default; else part of the device name
    output_device: str | None = None


class Vad(_Section):
    threshold: float = Field(0.5, gt=0, lt=1)
    silence_seconds: float = Field(0.7, gt=0)


class Stt(_Section):
    model: str = "small.en"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    vocab: list[str] = []


# Short names in config.yaml map to exact model IDs, so an alias can't drift.
MODEL_IDS = {
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
    "haiku": "claude-haiku-5-5",
}


def model_id(name: str) -> str:
    return MODEL_IDS.get(name, name)


class Brain(_Section):
    default_model: str = "sonnet"
    deep_model: str = "opus"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_spoken_sentences: int = Field(5, ge=1)


class Tts(_Section):
    voice: str = "en_US-ryan-medium"


class Brief(_Section):
    move_threshold_pct: float = Field(3, ge=0)


class Config(_Section):
    wake_word: WakeWord = WakeWord()
    followup_window_seconds: float = Field(8, ge=0)
    push_to_talk_hotkey: str | None = "ctrl+alt+space"  # None turns the global hotkey off
    # How to interrupt Jarvis while it talks (spec 6.6). "wake_word": say "Hey Jarvis"
    # (works on speakers). "voice": any speech, plus the wake word (needs headphones).
    barge_in: Literal["wake_word", "voice", "off"] = "wake_word"
    audio: Audio = Audio()
    vad: Vad = Vad()
    stt: Stt = Stt()
    brain: Brain = Brain()
    tts: Tts = Tts()
    brief: Brief = Brief()
    watchlist_file: Path = Path("notes/watchlist.md")
    theses_file: Path = Path("notes/theses.md")
    news_feeds: list[str] = []
    address_as: str | None = None  # how Jarvis greets you, e.g. "Mr. Dorman"
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
