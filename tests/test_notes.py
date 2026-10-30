from datetime import date
from pathlib import Path

import pytest

from tools.notes import NotePathError, append_note, read_notes, resolve_note


@pytest.fixture
def notes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "notes"
    d.mkdir()
    (d / "theses.md").write_text("# Theses\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("keep out", encoding="utf-8")
    return d


def test_resolve_adds_extension(notes_dir: Path):
    assert resolve_note(notes_dir, "theses") == (notes_dir / "theses.md").resolve()
    assert resolve_note(notes_dir, "theses.md") == (notes_dir / "theses.md").resolve()


@pytest.mark.parametrize(
    "bad",
    [
        "../secret",
        "..\\secret.md",
        "sub/../../secret",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "C:secret",
        "\\\\server\\share\\x",
        "",
        "  ",
    ],
)
def test_path_escapes_are_rejected(notes_dir: Path, bad: str):
    with pytest.raises(NotePathError):
        resolve_note(notes_dir, bad)


def test_read_rejects_escape_with_error_result(notes_dir: Path):
    r = read_notes(notes_dir, "../secret")
    assert "error" in r
    assert "keep out" not in str(r)


def test_read_and_list(notes_dir: Path):
    assert read_notes(notes_dir) == {"files": ["theses.md"]}
    assert read_notes(notes_dir, "theses")["text"] == "# Theses\n"
    assert read_notes(notes_dir, "missing")["error"] == "no such note"


def test_append_thesis_with_heading(notes_dir: Path):
    r = append_note(
        notes_dir, "theses", "Tokenized treasuries hit $20B.", "RWA growth", date(2026, 10, 29)
    )
    assert r == {
        "file": "theses.md",
        "saved": "Tokenized treasuries hit $20B.",
        "heading": "RWA growth",
    }
    text = (notes_dir / "theses.md").read_text(encoding="utf-8")
    assert text.endswith("## RWA growth\nTokenized treasuries hit $20B.\n_Saved 2026-10-29_\n")


def test_append_bullet_creates_file(notes_dir: Path):
    append_note(notes_dir, "inbox", "  check   basis trade  ", today=date(2026, 10, 29))
    assert (notes_dir / "inbox.md").read_text(
        encoding="utf-8"
    ) == "\n- 2026-10-29: check basis trade\n"


def test_append_rejects_empty_long_and_escape(notes_dir: Path):
    assert append_note(notes_dir, "inbox", "   ")["error"] == "nothing to save"
    assert "longer" in append_note(notes_dir, "inbox", "x" * 3000)["error"]
    assert "error" in append_note(notes_dir, "../secret", "pwned")
    assert (notes_dir.parent / "secret.md").read_text(encoding="utf-8") == "keep out"
