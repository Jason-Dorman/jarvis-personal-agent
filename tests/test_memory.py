import json
import sqlite3

import pytest

from jarvis.memory import Memory


@pytest.fixture
def mem():
    m = Memory(":memory:")
    yield m
    m.close()


def test_schema_has_all_tables(mem: Memory):
    names = {
        r["name"] for r in mem.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"price_snapshots", "tool_calls", "conversation"} <= names


def test_save_price(mem: Memory):
    mem.save_price("btc", 67000.5, "coingecko", change_pct=-2.1, ts="2026-10-29T12:00:00+00:00")
    row = mem.conn.execute("SELECT * FROM price_snapshots").fetchone()
    assert row["symbol"] == "BTC"
    assert row["price"] == 67000.5
    assert row["change_pct"] == -2.1


def test_log_tool_call_stores_json(mem: Memory):
    mem.log_tool_call("pegs", "get_peg", {"stablecoin": "USDC"}, {"price": 0.9998}, duration_ms=120)
    mem.log_tool_call("pegs", "get_peg", {"stablecoin": "XYZ"}, error="unknown stablecoin")
    ok, failed = mem.conn.execute("SELECT * FROM tool_calls ORDER BY id").fetchall()
    assert json.loads(ok["inputs"]) == {"stablecoin": "USDC"}
    assert json.loads(ok["outputs"]) == {"price": 0.9998}
    assert failed["outputs"] is None
    assert failed["error"] == "unknown stablecoin"


def test_log_turn_rejects_unknown_role(mem: Memory):
    mem.log_turn("s1", "user", "how's USDC?")
    mem.log_turn("s1", "assistant", "Holding at a dollar.", model="sonnet")
    assert mem.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        mem.log_turn("s1", "system", "nope")
