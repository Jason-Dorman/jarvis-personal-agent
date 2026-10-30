from datetime import date
from pathlib import Path

from jarvis.brief import assemble_brief, save_brief

SNAPSHOT = {
    "items": [
        {"symbol": "BTC", "price": 77168, "change_pct": -4.6},
        {"symbol": "SPY", "price": 772.31, "change_pct": 0.3},
    ],
    "errors": [{"symbol": "QQQ", "error": "no price"}],
}
ALERTS = {"alerts": [], "all_normal": True, "rules": "warn at 0.5%"}
MAJORS = [{"symbol": "USDT", "status": "normal"}, {"symbol": "USDC", "status": "normal"}]
HEADLINES = {"headlines": [{"title": f"H{i}"} for i in range(8)]}


def test_assemble_brief_sections():
    b = assemble_brief(SNAPSHOT, 3, ALERTS, MAJORS, HEADLINES, "## Thesis", date(2026, 10, 29))
    assert b["date"] == "2026-10-29"
    assert [m["symbol"] for m in b["watchlist"]["movers"]] == ["BTC"]
    assert b["watchlist"]["quiet"] == ["SPY"]
    assert b["watchlist"]["unavailable"] == ["QQQ"]
    assert b["pegs"]["all_normal"] is True
    assert b["pegs"]["majors"] == MAJORS
    assert len(b["headlines"]) == 5
    assert b["theses"] == "## Thesis"
    assert b["errors"] == {}


def test_assemble_brief_keeps_going_when_sections_fail():
    b = assemble_brief(
        {"error": "down"},
        3,
        {"error": "timed out"},
        {"error": "x"},
        {"error": "no feeds"},
        "",
        date(2026, 1, 2),
    )
    assert b["errors"] == {"watchlist": "down", "pegs": "timed out", "headlines": "no feeds"}
    assert b["watchlist"]["movers"] == []
    assert b["pegs"]["all_normal"] is None
    assert b["headlines"] == []


def test_save_brief_replaces_same_day(tmp_path: Path):
    day = date(2026, 10, 29)
    first = save_brief("First take.", day, tmp_path)
    second = save_brief("Second take.\n", day, tmp_path)
    assert first == second == tmp_path / "2026-10-29.md"
    assert second.read_text(encoding="utf-8") == "# Morning brief, 2026-10-29\n\nSecond take.\n"
