"""Stablecoin pegs and supply from the DefiLlama stablecoins API (no key needed).

Parsing is plain functions (tested against fixtures). `PegData` does the fetching.
The MCP wrapper is in `pegs_server.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from tools.common import TtlCache, error, now_iso, pct_change

STABLES_URL = "https://stablecoins.llama.fi/stablecoins"
CHARTS_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
PRICE_CHART_URL = "https://coins.llama.fi/chart/coingecko:{gecko_id}"

# Distance from $1, in percent.
WARN_PCT = 0.5
ALERT_PCT = 2.0
# list_peg_alerts skips coins smaller than this, so tiny illiquid tokens don't cry wolf.
ALERT_MIN_SUPPLY_USD = 1_000_000_000

ALIASES = {
    "TETHER": "USDT",
    "USD COIN": "USDC",
    "CIRCLE": "USDC",
    "PAYPAL": "PYUSD",
    "PAYPAL USD": "PYUSD",
    "SKY": "USDS",
    "ETHENA": "USDE",
    "RIPPLE USD": "RLUSD",
}

_PREV_FIELDS = {1: "circulatingPrevDay", 7: "circulatingPrevWeek", 30: "circulatingPrevMonth"}


def _usd(field: dict[str, Any] | None) -> float | None:
    if not field:
        return None
    v = field.get("peggedUSD")
    return float(v) if v is not None else None


def find_asset(assets: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Match by symbol, name, or CoinGecko id. Duplicated symbols resolve to the biggest."""
    q = query.strip().upper()
    q = ALIASES.get(q, q)
    hits = [
        a
        for a in assets
        if q in (str(a.get("symbol", "")).upper(), str(a.get("name", "")).upper())
        or q == str(a.get("gecko_id", "")).upper()
    ]
    if not hits:
        return None
    return max(hits, key=lambda a: _usd(a.get("circulating")) or 0)


def peg_status(price: float | None, peg_type: str) -> tuple[float | None, str]:
    """(deviation from $1 in percent, status). Only USD pegs are judged."""
    if price is None:
        return None, "unknown"
    if peg_type != "peggedUSD":
        return None, "not a USD peg"
    dev = round((price - 1) * 100, 3)
    if abs(dev) >= ALERT_PCT:
        return dev, "alert"
    if abs(dev) >= WARN_PCT:
        return dev, "warn"
    return dev, "normal"


def peg_summary(asset: dict[str, Any]) -> dict[str, Any]:
    price = asset.get("price")
    price = float(price) if price is not None else None
    dev, status = peg_status(price, asset.get("pegType", ""))
    supply = _usd(asset.get("circulating"))
    out: dict[str, Any] = {
        "symbol": asset.get("symbol"),
        "name": asset.get("name"),
        "price": None if price is None else round(price, 5),
        "deviation_pct": dev,
        "status": status,
        "supply_usd": None if supply is None else round(supply),
        "peg_mechanism": asset.get("pegMechanism"),
    }
    for days, key in _PREV_FIELDS.items():
        prev = _usd(asset.get(key))
        out[f"supply_change_{days}d_pct"] = (
            pct_change(supply, prev) if supply is not None and prev else None
        )
    return out


def list_alerts(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for a in assets:
        if (_usd(a.get("circulating")) or 0) < ALERT_MIN_SUPPLY_USD:
            continue
        s = peg_summary(a)
        if s["status"] in ("warn", "alert"):
            alerts.append(s)
    alerts.sort(key=lambda s: abs(s["deviation_pct"] or 0), reverse=True)
    return alerts


def supply_change_from_chart(points: list[dict[str, Any]], days: int) -> dict[str, Any] | None:
    """Daily supply points (oldest first) → change over the last `days` days."""
    series = [
        (int(p["date"]), _usd(p.get("totalCirculatingUSD")) or _usd(p.get("totalCirculating")))
        for p in points
        if p.get("date") is not None
    ]
    series = [(d, v) for d, v in series if v is not None]
    if len(series) < 2:
        return None
    end_date, end = series[-1]
    cutoff = end_date - days * 86400
    past = [(d, v) for d, v in series if d <= cutoff]
    start_date, start = past[-1] if past else series[0]
    window = [v for d, v in series if d >= start_date]
    return {
        "days": days,
        "start_usd": round(start),
        "end_usd": round(end),
        "change_usd": round(end - start),
        "change_pct": pct_change(end, start),
        "start_date": datetime.fromtimestamp(start_date, UTC).date().isoformat(),
        "end_date": datetime.fromtimestamp(end_date, UTC).date().isoformat(),
        "series_billions": [round(v / 1e9, 3) for v in window[-60:]],
    }


def parse_price_chart(data: dict[str, Any]) -> list[float]:
    coins = data.get("coins") or {}
    for coin in coins.values():
        return [round(float(p["price"]), 5) for p in coin.get("prices", []) if "price" in p]
    return []


class PegData:
    def __init__(self, http: httpx.AsyncClient, ttl_seconds: float = 60) -> None:
        self.http = http
        self._cache = TtlCache(ttl_seconds)

    async def _assets(self) -> list[dict[str, Any]]:
        cached = self._cache.get("assets")
        if cached is not None:
            return cached
        r = await self.http.get(STABLES_URL, params={"includePrices": "true"})
        r.raise_for_status()
        assets = r.json().get("peggedAssets", [])
        self._cache.put("assets", assets)
        return assets

    async def get_peg(self, stablecoin: str) -> dict[str, Any]:
        asset = find_asset(await self._assets(), stablecoin)
        if not asset:
            return error("unknown stablecoin", stablecoin=stablecoin)
        out = {**peg_summary(asset), "source": "DefiLlama", "as_of": now_iso()}
        out["price_24h"] = await self._price_history(asset.get("gecko_id"))
        return out

    async def get_supply_change(self, stablecoin: str, days: int = 7) -> dict[str, Any]:
        days = max(1, min(int(days), 365))
        asset = find_asset(await self._assets(), stablecoin)
        if not asset:
            return error("unknown stablecoin", stablecoin=stablecoin)
        r = await self.http.get(CHARTS_URL, params={"stablecoin": asset["id"]})
        r.raise_for_status()
        change = supply_change_from_chart(r.json(), days)
        if not change:
            return error("no supply history", stablecoin=asset.get("symbol"))
        return {
            "symbol": asset.get("symbol"),
            "name": asset.get("name"),
            **change,
            "source": "DefiLlama",
        }

    async def list_peg_alerts(self) -> dict[str, Any]:
        alerts = list_alerts(await self._assets())
        return {
            "alerts": alerts,
            "all_normal": not alerts,
            "rules": f"warn at {WARN_PCT}% off $1, alert at {ALERT_PCT}%, "
            f"coins over ${ALERT_MIN_SUPPLY_USD / 1e9:.0f}B supply",
            "source": "DefiLlama",
            "as_of": now_iso(),
        }

    async def majors(self, symbols: tuple[str, ...] = ("USDT", "USDC")) -> list[dict[str, Any]]:
        assets = await self._assets()
        return [peg_summary(a) for s in symbols if (a := find_asset(assets, s))]

    async def _price_history(self, gecko_id: str | None) -> list[float]:
        if not gecko_id:
            return []
        try:
            r = await self.http.get(
                PRICE_CHART_URL.format(gecko_id=gecko_id), params={"span": 24, "period": "1h"}
            )
            r.raise_for_status()
            return parse_price_chart(r.json())
        except httpx.HTTPError:
            return []  # the spark line is a nice-to-have
