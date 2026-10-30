"""Audio device plumbing shared by voice_in and voice_out.

On Windows, devices go through WASAPI with auto-convert on, so we can open the mic at
16 kHz and the speakers at Piper's rate even though the hardware runs at 48 kHz.
"""

from __future__ import annotations

from typing import Any, Literal

import sounddevice as sd

WASAPI = "Windows WASAPI"


def _wasapi_index() -> int | None:
    for i, api in enumerate(sd.query_hostapis()):
        if api["name"] == WASAPI:
            return i
    return None


def find_device(kind: Literal["input", "output"], name: str | None) -> int | None:
    """Device index for `name` (any part of the device name), or the default.

    Prefers WASAPI devices on Windows. Returns None to mean "PortAudio's default".
    Raises ValueError if a name was given and nothing matches, so a typo fails at startup.
    """
    api = _wasapi_index()
    channels = f"max_{kind}_channels"
    candidates = [
        (i, d)
        for i, d in enumerate(sd.query_devices())
        if d[channels] > 0 and (api is None or d["hostapi"] == api)
    ]
    if name:
        for i, d in candidates:
            if name.lower() in d["name"].lower():
                return i
        names = ", ".join(sorted({d["name"] for _, d in candidates}))
        raise ValueError(f"No {kind} device matches {name!r}. Found: {names}")
    if api is not None:
        default = sd.query_hostapis(api)[f"default_{kind}_device"]
        if default >= 0:
            return default
    return None


def device_name(index: int | None, kind: Literal["input", "output"]) -> str:
    info = sd.query_devices(index, kind) if index is not None else sd.query_devices(kind=kind)
    return str(info["name"])


def stream_settings(device: int | None) -> dict[str, Any]:
    """Extra stream kwargs: WASAPI auto-convert lets us pick our own sample rate."""
    if device is None or _wasapi_index() is None:
        return {}
    if sd.query_devices(device)["hostapi"] != _wasapi_index():
        return {}
    return {"extra_settings": sd.WasapiSettings(auto_convert=True)}
