"""Read and append markdown notes. Only files inside notes/ are reachable.

The MCP wrapper is in `notes_server.py`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from tools.common import error

MAX_NOTE_CHARS = 2000


class NotePathError(ValueError):
    pass


def resolve_note(notes_dir: Path, file: str) -> Path:
    """`theses` or `theses.md` → notes/theses.md. Rejects anything that leaves notes/."""
    name = (file or "").strip()
    if not name:
        raise NotePathError("no file name given")
    if (
        PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or ".." in PureWindowsPath(name).parts
    ):
        raise NotePathError("notes must stay inside the notes folder")
    if not name.lower().endswith(".md"):
        name += ".md"
    root = notes_dir.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise NotePathError("notes must stay inside the notes folder")
    return path


def list_notes(notes_dir: Path) -> list[str]:
    if not notes_dir.exists():
        return []
    root = notes_dir.resolve()
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))


def read_notes(notes_dir: Path, file: str | None = None) -> dict[str, Any]:
    if not file:
        return {"files": list_notes(notes_dir)}
    try:
        path = resolve_note(notes_dir, file)
    except NotePathError as exc:
        return error(str(exc), file=file)
    if not path.exists():
        return error("no such note", file=file, files=list_notes(notes_dir))
    rel = path.relative_to(notes_dir.resolve()).as_posix()
    return {"file": rel, "text": path.read_text(encoding="utf-8")}


def format_note(text: str, heading: str | None, today: date) -> str:
    """With a heading: a new `## heading` section. Without: a dated bullet."""
    if heading:
        return f"\n## {heading.strip()}\n{text}\n_Saved {today.isoformat()}_\n"
    return f"\n- {today.isoformat()}: {text}\n"


def append_note(
    notes_dir: Path,
    file: str,
    text: str,
    heading: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    text = " ".join((text or "").split())
    if not text:
        return error("nothing to save")
    if len(text) > MAX_NOTE_CHARS:
        return error(f"note is longer than {MAX_NOTE_CHARS} characters")
    try:
        path = resolve_note(notes_dir, file)
    except NotePathError as exc:
        return error(str(exc), file=file)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = format_note(text, heading, today or date.today())
    if existing and not existing.endswith("\n"):
        block = "\n" + block
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(block)
    rel = path.relative_to(notes_dir.resolve()).as_posix()
    return {"file": rel, "saved": text, "heading": heading}
