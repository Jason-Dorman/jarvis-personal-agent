"""Model files for voice, downloaded on first run into the gitignored models/ folder.

    uv run python -m jarvis.models     # fetch everything up front

Whisper models are fetched by faster-whisper itself into models/whisper.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from jarvis.config import MODELS_DIR

PIPER_DIR = MODELS_DIR / "piper"
WAKE_DIR = MODELS_DIR / "openwakeword"
WHISPER_DIR = MODELS_DIR / "whisper"

# openWakeWord's own release assets (the URLs its package lists, ONNX flavour).
WAKE_RELEASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
WAKE_FEATURES = ("melspectrogram.onnx", "embedding_model.onnx")
WAKE_MODELS = {
    "hey_jarvis": "hey_jarvis_v0.1.onnx",
    "alexa": "alexa_v0.1.onnx",
    "hey_mycroft": "hey_mycroft_v0.1.onnx",
}

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows without dev mode


@dataclass(frozen=True)
class WakeFiles:
    model: Path
    melspec: Path
    embedding: Path


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {dest.name}...", flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)
    return dest


def piper_voice(name: str) -> Path:
    """Path to the voice's .onnx file (its .onnx.json sits next to it)."""
    model = PIPER_DIR / f"{name}.onnx"
    if not (model.exists() and model.with_suffix(".onnx.json").exists()):
        from piper.download_voices import download_voice

        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading Piper voice {name}...", flush=True)
        download_voice(name, PIPER_DIR)
    return model


def wake_files(name: str) -> WakeFiles:
    if name not in WAKE_MODELS:
        raise ValueError(f"Unknown wake word model {name!r}. Pick one of: {', '.join(WAKE_MODELS)}")
    mel, emb = (_download(WAKE_RELEASE + f, WAKE_DIR / f) for f in WAKE_FEATURES)
    model = _download(WAKE_RELEASE + WAKE_MODELS[name], WAKE_DIR / WAKE_MODELS[name])
    return WakeFiles(model=model, melspec=mel, embedding=emb)


def main() -> None:
    from faster_whisper import download_model

    from jarvis.config import get_config

    cfg = get_config()
    piper_voice(cfg.tts.voice)
    wake_files(cfg.wake_word.model)
    print(f"Downloading whisper {cfg.stt.model} (if needed)...", flush=True)
    download_model(cfg.stt.model, cache_dir=str(WHISPER_DIR))
    print(f"Models ready in {MODELS_DIR}")


if __name__ == "__main__":
    main()
