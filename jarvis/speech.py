"""Split a streamed reply into sentences, so each one can be shown (and later spoken)
while the next is still being generated.

Decimals like 99.98, and abbreviations like U.S. or vs., don't end a sentence.
"""

from __future__ import annotations

import re

ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "approx", "inc", "corp",
    "co", "ltd", "jr", "sr", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec", "no", "u.s", "u.k", "est",
}  # fmt: skip

_END = re.compile(r"[.!?…]+[\"')\]]*")
_LAST_WORD = re.compile(r"([A-Za-z.]+)\.$")


def _is_abbreviation(text_before: str) -> bool:
    m = _LAST_WORD.search(text_before)
    if not m:
        return False
    word = m.group(1).lower()
    return word in ABBREVIATIONS or len(word) == 1


class SentenceSplitter:
    """Feed text deltas in; get finished sentences out."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sentence, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []

    def _find_cut(self) -> int | None:
        nl = self._buf.find("\n")
        for m in _END.finditer(self._buf):
            end = m.end()
            if nl != -1 and nl < m.start():
                break
            # Need to see what comes next before deciding.
            if end >= len(self._buf):
                break
            nxt = self._buf[end]
            if not nxt.isspace():
                continue  # 99.98, or "word.word"
            rest = self._buf[end:].lstrip()
            if not rest:
                break  # wait for the next word
            if m.group(0).startswith(".") and _is_abbreviation(self._buf[: m.start() + 1]):
                continue
            if rest[0].islower():
                continue
            return end
        if nl != -1:
            return nl + 1
        return None
