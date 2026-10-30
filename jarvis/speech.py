"""Text on its way to the voice.

`SentenceSplitter` cuts a streamed reply into sentences, so each one can be spoken while
the next is still being generated. Decimals like 99.98, and abbreviations like U.S. or
vs., don't end a sentence.

`speakable()` is the cleanup pass before Piper. The prompt already asks for spoken
numbers. This catches what slips through: tickers Piper would read as words ("USDC" came
out as "Husk"), $ amounts, signed percents, shorthand units, and stray markdown. Only the
audio gets this text. Captions show the sentence as written.
"""

from __future__ import annotations

import re
import unicodedata

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


# ---------------------------------------------------------------------------
# speakable()
# ---------------------------------------------------------------------------

# All-caps words people say as words, not letters. Everything else 2-5 caps is spelled.
SAY_AS_WORD = {
    "HYPE", "COIN", "SOL", "DOGE", "LINK", "PEPE", "NASDAQ", "NATO",
    "NOT", "AND", "THE", "ALL", "NO", "YES", "OK", "NEW", "BIG", "NOW",
}  # fmt: skip

SCALES = {"K": "thousand", "M": "million", "B": "billion", "T": "trillion"}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_UP_DOWN = r"(?:up|down|rose|fell|gained|lost|dropped|climbed|slipped|rallied|jumped|sank)"

_MARKDOWN = [
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # [text](url) -> text
    (re.compile(r"https?://\S+?(?=[.,;:!?]?(?:\s|$))"), "the link"),
    (re.compile(r"^\s*(?:#{1,6}|[-*•]|\d+\.)\s+", re.MULTILINE), ""),  # headings, bullets
    (re.compile(r"\*\*|__|`|(?<!\w)[*_]|[*_](?!\w)"), ""),  # bold, italics, code
]

_REWRITES = [
    # money with a scale: $1.2B, $3.4 million
    (
        re.compile(rf"\$({_NUM})\s?([KMBT])\b"),
        lambda m: f"{m[1]} {SCALES[m[2]]} dollars",
    ),
    (
        re.compile(rf"\$({_NUM})\s(thousand|million|billion|trillion)\b"),
        lambda m: f"{m[1]} {m[2]} dollars",
    ),
    (re.compile(r"\$(0?\.\d+)\b"), lambda m: _cents(m[1])),  # under a dollar -> cents
    (re.compile(rf"\$({_NUM})"), r"\1 dollars"),
    (re.compile(r"\b(\d+(?:\.\d+)?)([KMBT])\b"), lambda m: f"{m[1]} {SCALES[m[2]]}"),
    # signed percents: drop the sign after a direction word, else say it
    (re.compile(rf"\b({_UP_DOWN})\s+[+-](?=\d)", re.IGNORECASE), r"\1 "),
    (re.compile(r"(?<![\w.])\+(?=\d[\d.]*%)"), "up "),
    (re.compile(r"(?<![\w.])[-−](?=\d[\d.]*%)"), "down "),
    # units and shorthand
    (re.compile(r"\b(\d+)\s?h\b"), r"\1 hours"),
    (re.compile(r"\b(\d+)\s?d\b"), r"\1 days"),
    (re.compile(r"\b(\d+)\s?[Yy]\b"), r"\1 year"),
    (re.compile(r"\b1\s?bps?\b"), "1 basis point"),
    (re.compile(r"\bbps?\b"), "basis points"),
    (re.compile(r"\b([A-Z])-(?=[a-z])"), r"\1 "),  # T-bills -> T bills
    (re.compile(r"\bvs\.?(?=\s)"), "versus"),
    (re.compile(r"\be\.g\.(?=\s)"), "for example"),
    (re.compile(r"\bi\.e\.(?=\s)"), "that is"),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"[~≈]\s*"), "about "),
    # tickers and acronyms: spell them out
    (
        re.compile(r"\b[A-Z]{2,5}\b"),
        lambda m: m[0] if m[0] in SAY_AS_WORD else " ".join(m[0]),
    ),
]


def _cents(dollars: str) -> str:
    cents = round(float(dollars) * 100, 2)
    text = f"{cents:.2f}".rstrip("0").rstrip(".")
    return f"{text} cent" if text == "1" else f"{text} cents"


def _drop_symbols(text: str) -> str:
    return "".join(c for c in text if unicodedata.category(c) not in {"So", "Sk", "Cs", "Co"})


def speakable(text: str) -> str:
    """Rewrite one sentence so Piper says it the way a person would."""
    for pattern, repl in _MARKDOWN:
        text = pattern.sub(repl, text)
    for pattern, repl in _REWRITES:
        text = pattern.sub(repl, text)
    text = _drop_symbols(text)
    return re.sub(r"\s+", " ", text).strip()
