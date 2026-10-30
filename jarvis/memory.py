"""SQLite memory (spec 6.8): price snapshots, the tool call log, the conversation log,
and per-turn timings (spec 10).

Timestamps are ISO-8601 UTC strings. Tool inputs and outputs are stored as JSON text.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    price       REAL NOT NULL,
    change_pct  REAL,              -- 24h change, when the source gives one
    source      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_symbol_ts ON price_snapshots (symbol, ts);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    tool        TEXT NOT NULL,
    fn          TEXT NOT NULL,
    inputs      TEXT NOT NULL,     -- JSON
    outputs     TEXT,              -- JSON, null if the call failed before returning
    error       TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS conversation (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text        TEXT NOT NULL,
    model       TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation (session_id, ts);

CREATE TABLE IF NOT EXISTS turn_timings (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,     -- wake | ptt | follow_up | typed
    question    TEXT,
    model       TEXT,
    tool_calls  INTEGER NOT NULL,
    interrupted INTEGER NOT NULL,  -- 1 if barge-in cut the answer off
    spans       TEXT NOT NULL      -- JSON: span name -> ms (jarvis/timing.py)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Memory:
    """One connection to jarvis.db. Creates the schema on open."""

    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def save_price(
        self,
        symbol: str,
        price: float,
        source: str,
        change_pct: float | None = None,
        ts: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO price_snapshots (ts, symbol, price, change_pct, source)"
                " VALUES (?, ?, ?, ?, ?)",
                (ts or _now(), symbol.upper(), price, change_pct, source),
            )

    def log_tool_call(
        self,
        tool: str,
        fn: str,
        inputs: dict[str, Any],
        outputs: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
        ts: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO tool_calls (ts, tool, fn, inputs, outputs, error, duration_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts or _now(),
                    tool,
                    fn,
                    json.dumps(inputs, default=str),
                    None if outputs is None else json.dumps(outputs, default=str),
                    error,
                    duration_ms,
                ),
            )

    def log_turn(
        self,
        session_id: str,
        role: str,
        text: str,
        model: str | None = None,
        ts: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO conversation (ts, session_id, role, text, model)"
                " VALUES (?, ?, ?, ?, ?)",
                (ts or _now(), session_id, role, text, model),
            )

    def log_timing(self, record: dict[str, Any], ts: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO turn_timings"
                " (ts, source, question, model, tool_calls, interrupted, spans)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts or _now(),
                    record["source"],
                    record.get("question"),
                    record.get("model"),
                    record["tool_calls"],
                    int(bool(record.get("interrupted"))),
                    json.dumps(record["spans"]),
                ),
            )

    def recent_timings(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first, spans decoded."""
        rows = self.conn.execute(
            "SELECT * FROM turn_timings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{**dict(r), "spans": json.loads(r["spans"])} for r in rows]
