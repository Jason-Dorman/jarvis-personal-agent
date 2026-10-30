"""MCP wrapper for notes. Logic lives in notes.py."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from mcp.types import ToolAnnotations

from tools import notes
from tools.common import safe_tool
from tools.services import Services


def tools(svc: Services) -> list[SdkMcpTool[Any]]:
    @tool(
        "read_notes",
        "Read a note file from the notes folder (e.g. theses, watchlist). Omit file to list them.",
        {"type": "object", "properties": {"file": {"type": "string"}}},
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    @safe_tool
    async def read_notes(args: dict[str, Any]) -> dict[str, Any]:
        return notes.read_notes(svc.notes_dir, args.get("file"))

    @tool(
        "append_note",
        "Save a thought to a note file. Theses go to theses with a short heading. "
        "Other thoughts go to inbox as a dated bullet.",
        {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "theses, inbox, or another note name"},
                "text": {"type": "string", "description": "The thought, in the user's words"},
                "heading": {"type": "string", "description": "A short title. Use it for theses."},
            },
            "required": ["file", "text"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    @safe_tool
    async def append_note(args: dict[str, Any]) -> dict[str, Any]:
        return notes.append_note(svc.notes_dir, args["file"], args["text"], args.get("heading"))

    return [read_notes, append_note]
