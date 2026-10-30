import httpx
import pytest
from conftest import load_json

from jarvis.memory import Memory
from tools.markets import (
    MarketData,
    WatchItem,
    movers_from_snapshot,
    parse_coingecko_markets,
    parse_watchlist,
    pick_search_match,
    quote_from_closes,
)

IDS = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "hyperliquid": "HYPE"}


def test_parse_watchlist_reads_kinds_and_skips_prose():
    text = "# Watchlist\n\nOne symbol per line.\n\n- btc\n- SPY (stock)\n- ^TNX\n- BTC\n* ETH\n"
    assert parse_watchlist(text) == [
        WatchItem("BTC", "crypto"),
        WatchItem("SPY", "stock"),
        WatchItem("^TNX", "stock"),
        WatchItem("ETH", "crypto"),
    ]


def test_repo_watchlist_parses():
    from jarvis.config import get_config

    items = parse_watchlist(get_config().watchlist_file.read_text(encoding="utf-8"))
    assert WatchItem("BTC", "crypto") in items
    assert WatchItem("SPY", "stock") in items


def test_parse_coingecko_markets():
    quotes = parse_coingecko_markets(load_json("coingecko_markets.json"), IDS)
    btc = quotes["BTC"]
    assert btc.name == "Bitcoin"
    assert btc.price == 77168
    assert btc.change_pct == -4.59
    assert btc.change_label == "24h"
    assert 2 <= len(btc.spark) <= 48
    assert set(quotes) == {"BTC", "ETH", "SOL", "HYPE"}


def test_parse_coingecko_skips_unrequested_ids():
    quotes = parse_coingecko_markets(load_json("coingecko_markets.json"), {"bitcoin": "BTC"})
    assert list(quotes) == ["BTC"]


def test_pick_search_match_prefers_exact_symbol_by_rank():
    search = {
        "coins": [
            {"id": "basedhype", "symbol": "BASEDHYPE", "market_cap_rank": 599},
            {"id": "fakehype", "symbol": "HYPE", "market_cap_rank": 900},
            {"id": "hyperliquid", "symbol": "HYPE", "market_cap_rank": 10},
        ]
    }
    assert pick_search_match("HYPE", search) == "hyperliquid"
    assert pick_search_match("NOPE", search) is None


def test_quote_from_closes():
    closes = load_json("yfinance_spy_closes.json")["closes"]
    q = quote_from_closes("SPY", "SPY", closes)
    assert q.price == 772.31
    assert q.change_pct == round((772.31 - 769.66) / 769.66 * 100, 2)
    assert q.change_label == "1d"
    assert q.spark[-1] == 772.31
    assert quote_from_closes("SPY", "SPY", []) is None


def test_movers_filter_and_sort():
    snapshot = {
        "items": [
            {"symbol": "BTC", "change_pct": -4.6},
            {"symbol": "SPY", "change_pct": 0.3},
            {"symbol": "HYPE", "change_pct": 12.5},
            {"symbol": "X", "change_pct": None},
        ],
        "errors": [],
    }
    out = movers_from_snapshot(snapshot, 3)
    assert [m["symbol"] for m in out["movers"]] == ["HYPE", "BTC"]
    assert out["quiet"] == ["SPY", "X"]


def _coingecko_transport(calls: list[str]) -> httpx.MockTransport:
    rows = load_json("coingecko_markets.json")

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/coins/markets"):
            ids = request.url.params["ids"].split(",")
            return httpx.Response(200, json=[r for r in rows if r["id"] in ids])
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"coins": []})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_get_price_caches_and_saves_snapshot():
    calls: list[str] = []
    mem = Memory(":memory:")
    async with httpx.AsyncClient(transport=_coingecko_transport(calls)) as http:
        md = MarketData(http, memory=mem)
        first = await md.get_price("btc")
        second = await md.get_price("BTC")
    assert first["price"] == second["price"] == 77168
    assert calls.count("/api/v3/coins/markets") == 1
    rows = mem.conn.execute("SELECT symbol, source FROM price_snapshots").fetchall()
    assert [tuple(r) for r in rows] == [("BTC", "CoinGecko")]


@pytest.mark.asyncio
async def test_unknown_coin_returns_error_not_exception():
    async with httpx.AsyncClient(transport=_coingecko_transport([])) as http:
        md = MarketData(http)
        r = await md.get_price("ZZZZ", asset_type="crypto")
    assert r["error"] == "unknown coin symbol"


@pytest.mark.asyncio
async def test_source_outage_becomes_error():
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        md = MarketData(http)
        r = await md.get_watchlist_snapshot([WatchItem("BTC", "crypto")])
    assert r["items"] == []
    assert "rate limited" in r["errors"][0]["error"]


@pytest.mark.asyncio
async def test_stalled_yahoo_gives_errors_instead_of_hanging(monkeypatch):
    import time

    import tools.markets as markets

    def stalled(symbol, lock_timeout):
        time.sleep(1)
        return symbol, [1.0, 2.0]

    monkeypatch.setattr(markets, "_yf_closes", stalled)
    monkeypatch.setattr(markets, "YF_BUDGET_SECONDS", 0.2)
    async with httpx.AsyncClient() as http:
        md = MarketData(http)
        r = await md.get_watchlist_snapshot([WatchItem("SPY", "stock"), WatchItem("QQQ", "stock")])
    assert r["items"] == []
    assert [e["symbol"] for e in r["errors"]] == ["SPY", "QQQ"]
    assert all("too slow" in e["error"] for e in r["errors"])
