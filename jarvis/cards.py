"""HUD panels built from tool results (spec 15.4, 15.6).

One builder per (tool, fn). Every number on a card comes from the tool's own result,
never from the model's words. A result with an `error` gets no card.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Card = dict[str, Any]
Builder = Callable[[dict[str, Any]], list[Card]]


def _price_decimals(price: float) -> int:
    if abs(price) >= 1000:
        return 0
    if abs(price) >= 1:
        return 2
    return 4


def _supply_billions(usd: float | None) -> float | None:
    return None if usd is None else round(usd / 1e9, 2)


def _peg_status(status: str | None) -> str | None:
    return {"warn": "warn", "alert": "alert"}.get(status or "")


# ---- markets ---------------------------------------------------------------
def price_card(r: dict[str, Any]) -> list[Card]:
    sym = r["symbol"]
    keys = [sym.lower().lstrip("^")]
    if r.get("name") and r["name"].lower() != keys[0]:
        keys.append(r["name"].lower())
    return [
        {
            "id": f"price-{sym.lower()}",
            "tool": "markets",
            "kind": "metric",
            "title": f"{sym} price",
            "keys": keys,
            "value": r["price"],
            "decimals": _price_decimals(r["price"]),
            "unit": "" if sym.startswith("^") else "USD",
            "delta": r.get("change_pct"),
            "delta_label": r.get("change_label", ""),
            "spark": r.get("spark") or None,
            "source": r.get("source"),
        }
    ]


def _rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label": q["symbol"],
            "value": q["price"],
            "decimals": _price_decimals(q["price"]),
            "delta": q.get("change_pct"),
        }
        for q in items
    ]


def watchlist_card(r: dict[str, Any]) -> list[Card]:
    if not r.get("items"):
        return []
    return [
        {
            "id": "watchlist",
            "tool": "markets",
            "kind": "metrics",
            "title": "Watchlist",
            "keys": ["watchlist"],
            "rows": _rows(r["items"]),
            "source": "CoinGecko, Yahoo Finance",
        }
    ]


def movers_card(r: dict[str, Any]) -> list[Card]:
    movers = r.get("movers") or []
    if not movers:
        return [
            {
                "id": "movers",
                "tool": "markets",
                "kind": "note",
                "title": "Watchlist quiet",
                "keys": ["watchlist", "quiet"],
                "text": f"Nothing moved more than {r.get('threshold_pct', 0):g}%.",
            }
        ]
    return [
        {
            "id": "movers",
            "tool": "markets",
            "kind": "metrics",
            "title": "Watchlist movers",
            "keys": ["watchlist", "movers", "moved"],
            "rows": _rows(movers),
            "source": "CoinGecko, Yahoo Finance",
        }
    ]


# ---- pegs ------------------------------------------------------------------
def peg_card(r: dict[str, Any]) -> list[Card]:
    sym = r["symbol"]
    keys = [sym.lower()]
    if r.get("name") and r["name"].lower() != sym.lower():
        keys.append(r["name"].lower())
    card: Card = {
        "id": f"peg-{sym.lower()}",
        "tool": "pegs",
        "kind": "metric",
        "title": f"{sym} peg",
        "keys": keys,
        "value": r.get("price"),
        "decimals": 4,
        "unit": "USD",
        "delta": r.get("supply_change_7d_pct"),
        "delta_label": "supply, 7 days",
        "spark": r.get("price_24h") or None,
        "band": [0.995, 1.005],
        "source": "DefiLlama",
    }
    if status := _peg_status(r.get("status")):
        card["status"] = status
    return [card]


def supply_card(r: dict[str, Any]) -> list[Card]:
    sym = r["symbol"]
    return [
        {
            "id": f"supply-{sym.lower()}-{r['days']}d",
            "tool": "pegs",
            "kind": "metric",
            "title": f"{sym} supply",
            "keys": [sym.lower(), "supply"],
            "value": _supply_billions(r.get("end_usd")),
            "decimals": 2,
            "unit": "B USD",
            "delta": r.get("change_pct"),
            "delta_label": f"{r['days']} days",
            "spark": r.get("series_billions") or None,
            "source": "DefiLlama",
        }
    ]


def _alert_text(a: dict[str, Any]) -> str:
    return f"{a['symbol']} at ${a['price']:.4f}, {a['deviation_pct']:+.2f}% from $1."


def peg_alerts_cards(r: dict[str, Any]) -> list[Card]:
    alerts = r.get("alerts") or []
    if not alerts:
        return [
            {
                "id": "pegs-normal",
                "tool": "pegs",
                "kind": "note",
                "title": "Pegs normal",
                "keys": ["peg", "pegs"],
                "text": "No large stablecoin is off its peg.",
                "source": "DefiLlama",
            }
        ]
    return [
        {
            "id": f"peg-alert-{a['symbol'].lower()}",
            "tool": "pegs",
            "kind": "alert" if a["status"] == "alert" else "note",
            "status": _peg_status(a["status"]),
            "title": f"{a['symbol']} off peg",
            "keys": [a["symbol"].lower()],
            "text": _alert_text(a),
            "source": "DefiLlama",
        }
        for a in alerts[:4]
    ]


# ---- news ------------------------------------------------------------------
def headlines_card(r: dict[str, Any], limit: int = 5) -> list[Card]:
    items = (r.get("headlines") or [])[:limit]
    if not items:
        return []
    topic = r.get("topic")
    title = "Headlines" if not topic or topic == "all" else f"Headlines: {topic}"
    return [
        {
            "id": "headlines",
            "tool": "news",
            "kind": "headlines",
            "title": title,
            "keys": ["headline", "headlines", "news"],
            "items": [{"title": h["title"], "source": h["source"], "age": h["age"]} for h in items],
        }
    ]


# ---- notes -----------------------------------------------------------------
def saved_note_card(r: dict[str, Any]) -> list[Card]:
    name = r["file"].removesuffix(".md")
    return [
        {
            "id": "note-saved",
            "tool": "notes",
            "kind": "note",
            "title": f"Saved to {name}",
            "keys": ["saved", name],
            "text": r["saved"],
        }
    ]


# ---- brief -----------------------------------------------------------------
def brief_cards(r: dict[str, Any]) -> list[Card]:
    cards: list[Card] = []
    wl = r.get("watchlist") or {}
    if "watchlist" not in (r.get("errors") or {}):
        cards += movers_card(
            {"movers": wl.get("movers"), "threshold_pct": r.get("move_threshold_pct")}
        )
    pegs = r.get("pegs") or {}
    if pegs.get("all_normal") is not None:
        cards += peg_alerts_cards({"alerts": pegs.get("alerts")})
    cards += headlines_card({"headlines": r.get("headlines")}, limit=3)
    for c in cards:
        c["tool"] = "brief"
    return cards


BUILDERS: dict[tuple[str, str], Builder] = {
    ("markets", "get_price"): price_card,
    ("markets", "get_watchlist_snapshot"): watchlist_card,
    ("markets", "get_movers"): movers_card,
    ("pegs", "get_peg"): peg_card,
    ("pegs", "get_supply_change"): supply_card,
    ("pegs", "list_peg_alerts"): peg_alerts_cards,
    ("news", "get_headlines"): headlines_card,
    ("notes", "append_note"): saved_note_card,
    ("brief", "get_brief_data"): brief_cards,
}


def cards_for(tool: str, fn: str, result: Any) -> list[Card]:
    """Panels for one tool result. Empty when the tool failed or has nothing to show."""
    builder = BUILDERS.get((tool, fn))
    if not builder or not isinstance(result, dict) or result.get("error"):
        return []
    try:
        return builder(result)
    except (KeyError, TypeError, ValueError):
        return []  # an odd result shape must never break the turn


def ticker_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Watchlist snapshot → the HUD ticker's `items`."""
    return [
        {
            "symbol": q["symbol"],
            "price": q["price"],
            "decimals": _price_decimals(q["price"]),
            "delta": q.get("change_pct") or 0,
        }
        for q in snapshot.get("items", [])
    ]


def panel_hint(cards: list[Card]) -> str:
    """Tell the brain which panels just went on screen and the words that reveal them."""
    if not cards:
        return ""
    parts = [f"'{c['title']}' (say: {', '.join(c.get('keys') or [])})" for c in cards]
    return "Panels on screen: " + "; ".join(parts) + ". Name each one when you speak about it."
