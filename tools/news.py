"""Headlines from the RSS feeds in config.yaml, plus short article summaries.

Parsing is plain functions (tested against fixtures). `NewsData` does the fetching.
The MCP wrapper is in `news_server.py`.
"""

from __future__ import annotations

import asyncio
import calendar
import html
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from tools.common import TtlCache, describe_http_error, error, now_iso

log = logging.getLogger("jarvis.tools.news")

MAX_SUMMARY_CHARS = 1500
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def clean_text(raw: str | None) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", raw or ""))).strip()


@dataclass
class Headline:
    title: str
    source: str
    link: str
    published: datetime | None
    summary: str

    def to_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "link": self.link,
            "published": self.published.isoformat() if self.published else None,
            "age": age_label(self.published, now),
            "summary": self.summary[:280],
        }


def _source_name(feed_title: str, url: str) -> str:
    title = feed_title.split(":")[0].strip()
    if title.upper().startswith("FRB"):
        return "Federal Reserve"
    return title or (urlparse(url).hostname or url)


def parse_feed(content: bytes, url: str) -> list[Headline]:
    parsed = feedparser.parse(content)
    source = _source_name(parsed.feed.get("title", ""), url)
    out = []
    for e in parsed.entries:
        title = clean_text(e.get("title"))
        if not title:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(stamp), UTC) if stamp else None
        out.append(
            Headline(
                title=title,
                source=source,
                link=e.get("link", ""),
                published=published,
                summary=clean_text(e.get("summary") or e.get("description")),
            )
        )
    return out


def age_label(published: datetime | None, now: datetime) -> str:
    if not published:
        return ""
    minutes = max(0, int((now - published).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 48 * 60:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


def _stem(word: str) -> str:
    word = word.lower()
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


_BROAD_TOPICS = {"", "all", "any", "top", "news", "latest", "headlines"}


def matches_topic(h: Headline, topic: str | None) -> bool:
    """Every topic word (plural-insensitive) appears in the title or summary."""
    if not topic or topic.strip().lower() in _BROAD_TOPICS:
        return True
    words = {_stem(w) for w in re.findall(r"[A-Za-z0-9$]+", topic) if len(w) > 1}
    haystack = {_stem(w) for w in re.findall(r"[A-Za-z0-9$]+", f"{h.title} {h.summary}")}
    return words <= haystack


def filter_headlines(
    items: list[Headline], topic: str | None, hours: float, now: datetime, limit: int
) -> list[Headline]:
    cutoff = now - timedelta(hours=hours)
    picked = [
        h
        for h in items
        if (h.published is None or h.published >= cutoff) and matches_topic(h, topic)
    ]
    picked.sort(key=lambda h: h.published or datetime.min.replace(tzinfo=UTC), reverse=True)
    seen: set[str] = set()
    unique = []
    for h in picked:
        key = h.title.lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique[:limit]


class _ArticleText(HTMLParser):
    """Pulls the title, meta description, and paragraph text out of a page."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.paragraphs: list[str] = []
        self._in: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("script", "style", "nav", "footer", "aside"):
            self._skip += 1
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = clean_text(a.get("content"))
        elif tag in ("title", "p") and not self._skip:
            self._in, self._buf = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "aside"):
            self._skip = max(0, self._skip - 1)
        elif tag == self._in:
            text = clean_text("".join(self._buf))
            if tag == "title" and not self.title:
                self.title = text
            elif tag == "p" and len(text) > 40:
                self.paragraphs.append(text)
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in and not self._skip:
            self._buf.append(data)


def extract_article(page: str) -> dict[str, str]:
    p = _ArticleText()
    p.feed(page)
    text = " ".join(p.paragraphs)[:MAX_SUMMARY_CHARS]
    return {"title": p.title, "description": p.description, "text": text}


def is_public_http_url(url: str) -> bool:
    """Only fetch normal web pages, never localhost or the private network."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_global


class NewsData:
    def __init__(self, http: httpx.AsyncClient, feeds: list[str], ttl_seconds: float = 300) -> None:
        self.http = http
        self.feeds = feeds
        self._cache = TtlCache(ttl_seconds)

    async def _fetch_feed(self, url: str) -> list[Headline] | str:
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        try:
            r = await self.http.get(url)
            r.raise_for_status()
        except Exception as exc:
            log.warning("feed %s failed: %s", url, exc)
            return describe_http_error(exc)
        items = parse_feed(r.content, url)
        self._cache.put(url, items)
        return items

    async def all_headlines(self) -> tuple[list[Headline], list[dict[str, str]]]:
        results = await asyncio.gather(*(self._fetch_feed(u) for u in self.feeds))
        items: list[Headline] = []
        errors = []
        for url, res in zip(self.feeds, results, strict=True):
            if isinstance(res, str):
                errors.append({"feed": url, "error": res})
            else:
                items.extend(res)
        return items, errors

    async def get_headlines(
        self, topic: str | None = None, hours: float = 24, limit: int = 8
    ) -> dict[str, Any]:
        if not self.feeds:
            return error("no news feeds configured")
        items, errors = await self.all_headlines()
        if not items and errors:
            return error("no feeds answered", feed_errors=errors)
        now = datetime.now(UTC)
        picked = filter_headlines(items, topic, hours, now, max(1, min(limit, 20)))
        return {
            "topic": topic or "all",
            "hours": hours,
            "headlines": [h.to_dict(now) for h in picked],
            "feed_errors": errors,
            "as_of": now_iso(),
        }

    async def get_article_summary(self, url: str) -> dict[str, Any]:
        if not is_public_http_url(url):
            return error("only public http(s) article links can be read", url=url)
        items, _ = await self.all_headlines()
        from_feed = next((h for h in items if h.link == url), None)
        try:
            r = await self.http.get(url)
            r.raise_for_status()
            page = extract_article(r.text)
        except Exception as exc:
            if from_feed:  # fall back to what the feed said
                page = {"title": from_feed.title, "description": from_feed.summary, "text": ""}
            else:
                return error(describe_http_error(exc), url=url)
        return {
            "url": url,
            "title": page["title"] or (from_feed.title if from_feed else ""),
            "source": from_feed.source if from_feed else urlparse(url).hostname,
            "description": page["description"] or (from_feed.summary if from_feed else ""),
            "text": page["text"],
        }
