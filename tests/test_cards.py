from conftest import load_json

from jarvis.cards import cards_for, panel_hint, ticker_items
from tools.pegs import find_asset, list_alerts, peg_summary


def test_error_results_get_no_card():
    assert cards_for("pegs", "get_peg", {"error": "unknown stablecoin"}) == []
    assert cards_for("pegs", "nope", {"symbol": "X"}) == []
    assert cards_for("pegs", "get_peg", "not a dict") == []
    assert cards_for("pegs", "get_peg", {"unexpected": "shape"}) == []


def test_price_card_numbers_come_from_the_result():
    r = {
        "symbol": "BTC",
        "name": "Bitcoin",
        "price": 77168,
        "change_pct": -4.59,
        "change_label": "24h",
        "source": "CoinGecko",
        "spark": [1, 2, 3],
    }
    [card] = cards_for("markets", "get_price", r)
    assert card["kind"] == "metric"
    assert card["value"] == 77168 and card["delta"] == -4.59 and card["decimals"] == 0
    assert card["keys"] == ["btc", "bitcoin"]
    assert card["spark"] == [1, 2, 3]


def test_peg_card_from_fixture(stablecoins):
    r = {**peg_summary(find_asset(stablecoins, "USDC")), "price_24h": [0.9994, 0.9991]}
    [card] = cards_for("pegs", "get_peg", r)
    assert card["title"] == "USDC peg"
    assert card["value"] == r["price"]
    assert card["delta"] == r["supply_change_7d_pct"]
    assert card["keys"] == ["usdc", "usd coin"]
    assert "status" not in card


def test_peg_alerts_cards(stablecoins):
    [card] = cards_for("pegs", "list_peg_alerts", {"alerts": list_alerts(stablecoins)})
    assert card["title"] == "USDf off peg"
    assert card["status"] == "warn"
    assert "-0.76%" in card["text"]
    [calm] = cards_for("pegs", "list_peg_alerts", {"alerts": [], "all_normal": True})
    assert calm["title"] == "Pegs normal"


def test_movers_and_quiet_watchlist():
    movers = {
        "movers": [{"symbol": "HYPE", "price": 89.89, "change_pct": 12.5}],
        "threshold_pct": 3,
    }
    [card] = cards_for("markets", "get_movers", movers)
    assert card["kind"] == "metrics"
    assert card["rows"] == [{"label": "HYPE", "value": 89.89, "decimals": 2, "delta": 12.5}]
    [quiet] = cards_for("markets", "get_movers", {"movers": [], "threshold_pct": 3})
    assert quiet["kind"] == "note" and "3%" in quiet["text"]


def test_supply_card():
    r = {
        "symbol": "USDC",
        "days": 7,
        "end_usd": 71_415_873_712,
        "change_pct": -1.13,
        "series_billions": [72.2, 71.4],
    }
    [card] = cards_for("pegs", "get_supply_change", r)
    assert card["value"] == 71.42 and card["unit"] == "B USD" and card["delta_label"] == "7 days"


def test_headlines_and_note_cards():
    r = {
        "topic": "stablecoin",
        "headlines": [{"title": "T", "source": "CoinDesk", "age": "2h ago", "link": "x"}],
    }
    [card] = cards_for("news", "get_headlines", r)
    assert card["title"] == "Headlines: stablecoin"
    assert card["items"] == [{"title": "T", "source": "CoinDesk", "age": "2h ago"}]
    [note] = cards_for(
        "notes", "append_note", {"file": "theses.md", "saved": "RWA up", "heading": "x"}
    )
    assert note["title"] == "Saved to theses" and note["text"] == "RWA up"
    assert cards_for("notes", "read_notes", {"file": "theses.md", "text": "..."}) == []


def test_brief_cards_cover_each_section(stablecoins):
    brief = {
        "move_threshold_pct": 3,
        "watchlist": {"movers": [{"symbol": "SOL", "price": 93.87, "change_pct": -7.4}]},
        "pegs": {"alerts": list_alerts(stablecoins), "all_normal": False},
        "headlines": [{"title": f"H{i}", "source": "S", "age": "1h ago"} for i in range(5)],
        "errors": {},
    }
    cards = cards_for("brief", "get_brief_data", brief)
    assert [c["title"] for c in cards] == ["Watchlist movers", "USDf off peg", "Headlines"]
    assert all(c["tool"] == "brief" for c in cards)
    assert len(cards[2]["items"]) == 3


def test_brief_cards_skip_failed_sections():
    brief = {
        "watchlist": {},
        "pegs": {"all_normal": None},
        "headlines": [],
        "errors": {"watchlist": "x"},
    }
    assert cards_for("brief", "get_brief_data", brief) == []


def test_ticker_items_from_fixture_shape():
    snap = {
        "items": [
            {"symbol": "BTC", "price": 77168, "change_pct": -4.6},
            {"symbol": "SPY", "price": 772.31, "change_pct": None},
        ]
    }
    assert ticker_items(snap) == [
        {"symbol": "BTC", "price": 77168, "decimals": 0, "delta": -4.6},
        {"symbol": "SPY", "price": 772.31, "decimals": 2, "delta": 0},
    ]


def test_panel_hint_names_keys():
    hint = panel_hint([{"title": "USDC peg", "keys": ["usdc", "usd coin"]}])
    assert "'USDC peg' (say: usdc, usd coin)" in hint
    assert panel_hint([]) == ""


def test_coingecko_fixture_round_trip_to_card():
    from tools.markets import parse_coingecko_markets

    q = parse_coingecko_markets(load_json("coingecko_markets.json"), {"solana": "SOL"})["SOL"]
    [card] = cards_for("markets", "get_price", q.to_dict())
    assert card["value"] == q.price and card["delta"] == q.change_pct
