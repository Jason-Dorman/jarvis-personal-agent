from datetime import UTC, datetime, timedelta

import pytest
from conftest import FIXTURES

from tools.news import (
    Headline,
    age_label,
    clean_text,
    extract_article,
    filter_headlines,
    is_public_http_url,
    parse_feed,
)

NOW = datetime(2026, 10, 30, 2, 0, tzinfo=UTC)


def _h(title: str, hours_ago: float | None, summary: str = "") -> Headline:
    published = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    return Headline(title, "Test", f"https://x.test/{title}", published, summary)


def test_parse_coindesk_feed():
    items = parse_feed((FIXTURES / "rss_coindesk.xml").read_bytes(), "https://www.coindesk.com/")
    assert len(items) == 25
    first = items[0]
    assert first.source == "CoinDesk"
    assert first.title.startswith("Binance hack drained wallets")
    assert first.published == datetime(2026, 10, 30, 0, 17, 10, tzinfo=UTC)
    assert first.link.startswith("https://www.coindesk.com/")


def test_fed_feed_gets_a_readable_source():
    rss = b"""<rss><channel><title>FRB: Press Release - All Releases</title>
    <item><title>Rates held</title><link>https://fed.test/a</link></item></channel></rss>"""
    assert parse_feed(rss, "https://fed.test/feed")[0].source == "Federal Reserve"


def test_filter_by_hours_topic_and_dedupe():
    items = [
        _h("Stablecoin bill advances", 2),
        _h("Bitcoin slides", 1, "Stablecoins see outflows"),
        _h("Old stablecoin news", 30),
        _h("Stablecoin bill advances", 3),
        _h("Fed holds rates", 5),
    ]
    picked = filter_headlines(items, "stablecoin", 24, NOW, 10)
    assert [h.title for h in picked] == ["Bitcoin slides", "Stablecoin bill advances"]
    assert len(filter_headlines(items, None, 24, NOW, 10)) == 3
    assert len(filter_headlines(items, "all", 48, NOW, 2)) == 2


def test_topic_needs_every_word():
    items = [_h("Fed holds rates", 1), _h("Fed approves bank merger", 1)]
    assert [h.title for h in filter_headlines(items, "fed rates", 24, NOW, 5)] == [
        "Fed holds rates"
    ]


@pytest.mark.parametrize(
    ("minutes", "label"), [(5, "5m ago"), (90, "1h ago"), (60 * 30, "30h ago"), (60 * 72, "3d ago")]
)
def test_age_label(minutes, label):
    assert age_label(NOW - timedelta(minutes=minutes), NOW) == label


def test_clean_text_strips_tags_and_entities():
    assert clean_text("<p>Fed &amp; banks</p>\n<b>agree</b>") == "Fed & banks agree"


def test_extract_article():
    page = """<html><head><title>Big news</title>
    <meta name="description" content="Short summary here."></head>
    <body><nav><p>Menu items that are long enough to count as a paragraph</p></nav>
    <p>First real paragraph of the article, long enough to keep.</p><p>tiny</p>
    <script>var x = "<p>not this</p>";</script></body></html>"""
    out = extract_article(page)
    assert out["title"] == "Big news"
    assert out["description"] == "Short summary here."
    assert out["text"] == "First real paragraph of the article, long enough to keep."


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://www.coindesk.com/a", True),
        ("http://localhost:8765/", False),
        ("http://127.0.0.1/", False),
        ("http://192.168.1.10/admin", False),
        ("file:///C:/secrets.txt", False),
        ("ftp://example.com/x", False),
    ],
)
def test_is_public_http_url(url, ok):
    assert is_public_http_url(url) is ok
