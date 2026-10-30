"""Global push-to-talk hotkey: hold Ctrl+Alt+Space anywhere to talk (spec 4, 11).

Windows only. It uses RegisterHotKey, which reports just this one combo, so nothing
here sees your other keys (no keyboard hook). Windows tells us when the combo is
pressed. We then watch the main key (Space) until it's released. While Jarvis runs,
other apps don't get this combo.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger("jarvis.hotkey")

MODIFIERS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 1
RELEASE_POLL_SECONDS = 0.02

_NAMED_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "pause": 0x13}


def _key_code(name: str) -> int | None:
    if name in _NAMED_KEYS:
        return _NAMED_KEYS[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())  # A-Z and 0-9 share their ASCII codes
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    return None


def parse_hotkey(combo: str) -> tuple[int, int]:
    """'ctrl+alt+space' → (modifier flags, virtual key code). ValueError if it's not one."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    flags = 0
    for mod in mods:
        if mod not in MODIFIERS:
            raise ValueError(f"unknown modifier {mod!r} in {combo!r}")
        flags |= MODIFIERS[mod]
    code = _key_code(key)
    if code is None:
        raise ValueError(f"unknown key {key!r} in {combo!r}")
    return flags, code


class Hotkey:
    """`Hotkey("ctrl+alt+space", on_change).start()`. `on_change(True)` on press and
    `on_change(False)` on release, both called from the hotkey's own thread."""

    def __init__(self, combo: str, on_change: Callable[[bool], None]) -> None:
        self.combo = combo
        self.flags, self.key = parse_hotkey(combo)
        self.on_change = on_change
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: str | None = None

    @staticmethod
    def supported() -> bool:
        return sys.platform == "win32"

    def start(self) -> None:
        """Raises RuntimeError if Windows won't give us the combo (another app has it)."""
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        if self._error:
            raise RuntimeError(self._error)

    def stop(self) -> None:
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(1)

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, self.flags | MOD_NOREPEAT, self.key):
            self._error = f"{self.combo} is already taken by another app"
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._hold(user32)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def _hold(self, user32: object) -> None:
        log.info("hotkey down")
        self.on_change(True)
        while user32.GetAsyncKeyState(self.key) & 0x8000:
            time.sleep(RELEASE_POLL_SECONDS)
        log.info("hotkey up")
        self.on_change(False)
