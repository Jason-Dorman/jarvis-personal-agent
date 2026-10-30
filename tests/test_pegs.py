import pytest
from conftest import load_json

from tools.pegs import (
    find_asset,
    list_alerts,
    parse_price_chart,
    peg_status,
    peg_summary,
    supply_change_from_chart,
)


def test_find_asset_by_symbol_name_and_alias(stablecoins):
    assert find_asset(stablecoins, "usdc")["name"] == "USD Coin"
    assert find_asset(stablecoins, "Tether")["symbol"] == "USDT"
    assert find_asset(stablecoins, "usd-coin")["symbol"] == "USDC"  # CoinGecko id
    assert find_asset(stablecoins, "nope") is None


def test_duplicate_symbols_pick_the_biggest(stablecoins):
    usds = [a for a in stablecoins if a["symbol"] == "USDS"]
    assert len(usds) > 1
    biggest = max(usds, key=lambda a: a["circulating"]["peggedUSD"])
    assert find_asset(stablecoins, "USDS") is biggest


@pytest.mark.parametrize(
    ("price", "status"),
    [(0.9991, "normal"), (1.004, "normal"), (0.994, "warn"), (1.006, "warn"), (0.97, "alert")],
)
def test_peg_status_thresholds(price, status):
    assert peg_status(price, "peggedUSD")[1] == status


def test_peg_status_skips_non_usd_and_missing():
    assert peg_status(1.08, "peggedEUR") == (None, "not a USD peg")
    assert peg_status(None, "peggedUSD") == (None, "unknown")


def test_peg_summary(stablecoins):
    s = peg_summary(find_asset(stablecoins, "USDC"))
    assert s["symbol"] == "USDC"
    assert s["price"] == 0.9991
    assert s["status"] == "normal"
    assert s["deviation_pct"] == -0.09
    assert s["supply_usd"] > 50e9
    assert isinstance(s["supply_change_7d_pct"], float)


def test_list_alerts_only_large_off_peg_coins(stablecoins):
    alerts = list_alerts(stablecoins)
    assert [a["symbol"] for a in alerts] == ["USDf"]
    assert alerts[0]["status"] == "warn"


def test_supply_change_from_chart():
    points = load_json("defillama_stablecoincharts_usdc.json")
    out = supply_change_from_chart(points, 7)
    assert out["days"] == 7
    end = points[-1]["totalCirculatingUSD"]["peggedUSD"]
    assert out["end_usd"] == round(end)
    assert out["change_pct"] == round(
        (out["end_usd"] - out["start_usd"]) / out["start_usd"] * 100, 2
    )
    assert len(out["series_billions"]) == 8  # 7 days back plus today


def test_supply_change_needs_two_points():
    assert supply_change_from_chart([], 7) is None


def test_parse_price_chart():
    prices = parse_price_chart(load_json("defillama_coins_chart_usdc.json"))
    assert len(prices) > 5
    assert all(0.99 < p < 1.01 for p in prices)
    assert parse_price_chart({}) == []
