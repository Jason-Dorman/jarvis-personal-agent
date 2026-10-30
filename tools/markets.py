"""Market prices: CoinGecko for crypto, yfinance for stocks, ETFs, and rates.

Parsing is plain functions (tested against fixtures). `MarketData` does the fetching,
caching, and price snapshots. The MCP wrapper is in `markets_server.py`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import warnings
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

# Import yfinance (and pandas/numpy with it) here, on the main thread, never lazily in a
# worker thread. On Windows a worker thread loading numpy's DLLs holds the OS loader lock,
# and every new thread, including the event loop's next to_thread, blocks behind it.
import yfinance as yf

from jarvis.memory import Memory
from tools.common import TtlCache, describe_http_error, error, now_iso, pct_change, round_price

log = logging.getLogger("jarvis.tools.markets")

# yfinance trips pandas deprecation warnings on every call. They'd spam the REPL.
# Set after the import above: pandas adds its own filter when it loads, which would win.
warnings.filterwarnings("ignore", module="yfinance")
warnings.filterwarnings("ignore", message=".*inplace keyword.*")

Kind = Literal["crypto", "stock"]

COINGECKO_URL = "https://api.coingecko.com/api/v3"

# Symbol → CoinGecko id for common coins. Anything else is looked up with /search.
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "HYPE": "hyperliquid",
    "XRP": "ripple",
    "BNB": "binancecoin",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "SUI": "sui",
    "TON": "the-open-network",
    "TRX": "tron",
    "LTC": "litecoin",
    "DOT": "polkadot",
    "AAVE": "aave",
    "UNI": "uniswap",
    "ENA": "ethena",
    "ONDO": "ondo-finance",
    "USDC": "usd-coin",
    "USDT": "tether",
}

SPARK_POINTS = 48


# ---------------------------------------------------------------------------
# Watchlist file
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WatchItem:
    symbol: str
    kind: Kind


_WATCH_LINE = re.compile(r"^\s*[-*]\s+(\^?[A-Za-z0-9.\-=]+)\s*(\((\w+)\))?\s*$")
_STOCK_TAGS = {"stock", "etf", "rate", "rates", "index"}


def parse_watchlist(text: str) -> list[WatchItem]:
    """`- BTC` is crypto. `- SPY (stock)` is a stock/ETF/rate. Other lines are ignored."""
    items: list[WatchItem] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _WATCH_LINE.match(line)
        if not m:
            continue
        symbol = m.group(1).upper()
        tag = (m.group(3) or "").lower()
        kind: Kind = "stock" if tag in _STOCK_TAGS or symbol.startswith("^") else "crypto"
        if symbol not in seen:
            seen.add(symbol)
            items.append(WatchItem(symbol, kind))
    return items


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
@dataclass
class Quote:
    symbol: str
    name: str
    price: float
    change_pct: float | None
    change_label: str  # what change_pct covers: "24h" (crypto) or "1d" (stock close to close)
    source: str
    kind: Kind
    spark: list[float] = field(default_factory=list)

    def to_dict(self, with_spark: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not with_spark:
            d.pop("spark")
        return d


def _thin(series: list[float], points: int = SPARK_POINTS) -> list[float]:
    vals = [float(v) for v in series if v is not None]
    if len(vals) <= points:
        return [round_price(v) for v in vals]
    step = len(vals) / points
    picked = [vals[int(i * step)] for i in range(points - 1)] + [vals[-1]]
    return [round_price(v) for v in picked]


def parse_coingecko_markets(rows: list[dict[str, Any]], symbols_by_id: dict[str, str]) -> dict:
    """CoinGecko /coins/markets rows → {SYMBOL: Quote}. Uses our symbol, not theirs."""
    out: dict[str, Quote] = {}
    for row in rows:
        cid = row.get("id")
        price = row.get("current_price")
        if cid not in symbols_by_id or price is None:
            continue
        symbol = symbols_by_id[cid]
        change = row.get("price_change_percentage_24h")
        out[symbol] = Quote(
            symbol=symbol,
            name=row.get("name") or symbol,
            price=round_price(float(price)),
            change_pct=None if change is None else round(float(change), 2),
            change_label="24h",
            source="CoinGecko",
            kind="crypto",
            spark=_thin((row.get("sparkline_in_7d") or {}).get("price") or []),
        )
    return out


def pick_search_match(symbol: str, search: dict[str, Any]) -> str | None:
    """Best CoinGecko id for a symbol from /search: exact symbol, highest market cap."""
    coins = [c for c in search.get("coins", []) if str(c.get("symbol", "")).upper() == symbol]
    if not coins:
        return None
    coins.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
    return coins[0].get("id")


def quote_from_closes(symbol: str, name: str, closes: list[float]) -> Quote | None:
    """Daily closes (oldest first) → Quote. The last close is today's price while open."""
    vals = [float(c) for c in closes if c is not None]
    if not vals:
        return None
    change = pct_change(vals[-1], vals[-2]) if len(vals) >= 2 else None
    return Quote(
        symbol=symbol,
        name=name,
        price=round_price(vals[-1]),
        change_pct=change,
        change_label="1d",
        source="Yahoo Finance",
        kind="stock",
        spark=_thin(vals),
    )


def movers_from_snapshot(snapshot: dict[str, Any], threshold_pct: float) -> dict[str, Any]:
    items = snapshot.get("items", [])
    big = [
        q
        for q in items
        if q.get("change_pct") is not None and abs(q["change_pct"]) >= threshold_pct
    ]
    big.sort(key=lambda q: abs(q["change_pct"]), reverse=True)
    return {
        "threshold_pct": threshold_pct,
        "movers": big,
        "quiet": [q["symbol"] for q in items if q not in big],
        "errors": snapshot.get("errors", []),
        "as_of": snapshot.get("as_of"),
    }


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
_yf_lock = threading.Lock()  # yfinance's own cache DB locks up under concurrent calls

# yfinance keeps a SQLite cache shared by every process on the machine. When another
# process holds its lock, a call can stall for minutes. Give up and say "unavailable".
YF_BUDGET_SECONDS = 10.0


def _yf_closes(symbol: str, lock_timeout: float) -> tuple[str, list[float]]:
    if not _yf_lock.acquire(timeout=max(0.0, lock_timeout)):
        raise TimeoutError("an earlier Yahoo Finance call is still running")
    try:
        hist = yf.Ticker(symbol).history(period="1mo", interval="1d")
    finally:
        _yf_lock.release()
    closes = [float(x) for x in hist["Close"].dropna().tolist()] if len(hist) else []
    return symbol, closes


class MarketData:
    """Fetches and caches quotes. Saves a price snapshot for every fresh quote."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        coingecko_key: str | None = None,
        memory: Memory | None = None,
        ttl_seconds: float = 45,
    ) -> None:
        self.http = http
        self.coingecko_key = coingecko_key or None
        self.memory = memory
        self._quotes = TtlCache(ttl_seconds)
        self._ids: dict[str, str] = dict(COINGECKO_IDS)
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    # ---- public calls (each returns a plain dict, never raises) ----
    async def get_price(
        self, symbol: str, asset_type: str = "auto", watchlist: list[WatchItem] | None = None
    ) -> dict[str, Any]:
        symbol = symbol.strip().upper().lstrip("$")
        if not symbol:
            return error("no symbol given")
        kind = self._guess_kind(symbol, asset_type, watchlist or [])
        quotes, errors = await self._quotes_for([WatchItem(symbol, kind)])
        if symbol in quotes:
            return {**quotes[symbol].to_dict(), "as_of": now_iso()}
        if asset_type == "auto" and kind == "stock":
            # Not a stock ticker Yahoo knows. Try it as a coin.
            quotes, errors = await self._quotes_for([WatchItem(symbol, "crypto")])
            if symbol in quotes:
                return {**quotes[symbol].to_dict(), "as_of": now_iso()}
        return error(errors.get(symbol, "no price found"), symbol=symbol)

    async def get_watchlist_snapshot(self, watchlist: list[WatchItem]) -> dict[str, Any]:
        if not watchlist:
            return error("the watchlist is empty")
        quotes, errors = await self._quotes_for(watchlist)
        return {
            "items": [
                quotes[w.symbol].to_dict(with_spark=False) for w in watchlist if w.symbol in quotes
            ],
            "errors": [{"symbol": s, "error": e} for s, e in errors.items()],
            "as_of": now_iso(),
        }

    async def get_movers(self, watchlist: list[WatchItem], threshold_pct: float) -> dict[str, Any]:
        snapshot = await self.get_watchlist_snapshot(watchlist)
        if "error" in snapshot:
            return snapshot
        return movers_from_snapshot(snapshot, threshold_pct)

    # ---- internals ----
    def _guess_kind(self, symbol: str, asset_type: str, watchlist: list[WatchItem]) -> Kind:
        if asset_type in ("crypto", "stock"):
            return asset_type  # type: ignore[return-value]
        for w in watchlist:
            if w.symbol == symbol:
                return w.kind
        if symbol in self._ids:
            return "crypto"
        return "stock"

    async def _quotes_for(self, items: list[WatchItem]) -> tuple[dict[str, Quote], dict[str, str]]:
        quotes: dict[str, Quote] = {}
        errors: dict[str, str] = {}
        missing: list[WatchItem] = []
        for w in items:
            cached = self._quotes.get(f"{w.kind}:{w.symbol}")
            if cached:
                quotes[w.symbol] = cached
            else:
                missing.append(w)
        crypto = [w.symbol for w in missing if w.kind == "crypto"]
        stocks = [w.symbol for w in missing if w.kind == "stock"]
        results = await asyncio.gather(self._crypto(crypto), self._stocks(stocks))
        for fresh, errs in results:
            errors.update(errs)
            for sym, q in fresh.items():
                quotes[sym] = q
                self._quotes.put(f"{q.kind}:{sym}", q)
                self._snapshot(q)
        return quotes, errors

    def _snapshot(self, q: Quote) -> None:
        if not self.memory:
            return
        try:
            self.memory.save_price(q.symbol, q.price, q.source, change_pct=q.change_pct)
        except Exception:
            log.exception("could not save price snapshot for %s", q.symbol)

    def _cg_headers(self) -> dict[str, str]:
        return {"x-cg-demo-api-key": self.coingecko_key} if self.coingecko_key else {}

    async def _coingecko_id(self, symbol: str) -> str | None:
        if symbol in self._ids:
            return self._ids[symbol]
        r = await self.http.get(
            f"{COINGECKO_URL}/search", params={"query": symbol}, headers=self._cg_headers()
        )
        r.raise_for_status()
        cid = pick_search_match(symbol, r.json())
        if cid:
            self._ids[symbol] = cid
        return cid

    async def _crypto(self, symbols: list[str]) -> tuple[dict[str, Quote], dict[str, str]]:
        if not symbols:
            return {}, {}
        errors: dict[str, str] = {}
        ids: dict[str, str] = {}
        try:
            for sym in symbols:
                cid = await self._coingecko_id(sym)
                if cid:
                    ids[cid] = sym
                else:
                    errors[sym] = "unknown coin symbol"
            if not ids:
                return {}, errors
            r = await self.http.get(
                f"{COINGECKO_URL}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(ids),
                    "sparkline": "true",
                    "price_change_percentage": "24h",
                },
                headers=self._cg_headers(),
            )
            r.raise_for_status()
            quotes = parse_coingecko_markets(r.json(), ids)
        except Exception as exc:
            log.warning("coingecko failed: %s", exc)
            msg = describe_http_error(exc)
            return {}, {**errors, **{s: msg for s in symbols if s not in errors}}
        for sym in ids.values():
            if sym not in quotes:
                errors[sym] = "no price from CoinGecko"
        return quotes, errors

    async def _stocks(self, symbols: list[str]) -> tuple[dict[str, Quote], dict[str, str]]:
        quotes: dict[str, Quote] = {}
        errors: dict[str, str] = {}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + YF_BUDGET_SECONDS
        for sym in symbols:
            left = deadline - loop.time()
            if left <= 0:
                errors[sym] = "Yahoo Finance is too slow right now"
                continue
            try:
                _, closes = await asyncio.wait_for(asyncio.to_thread(_yf_closes, sym, left), left)
            except TimeoutError:
                log.warning("yfinance timed out for %s", sym)
                errors[sym] = "Yahoo Finance is too slow right now"
                continue
            except Exception as exc:
                log.warning("yfinance failed for %s: %s", sym, exc)
                errors[sym] = "Yahoo Finance did not answer"
                continue
            q = quote_from_closes(sym, sym, closes)
            if q:
                quotes[sym] = q
            else:
                errors[sym] = "no price from Yahoo Finance"
        return quotes, errors
