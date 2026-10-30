"""The data services every tool shares: one HTTP client, one cache per source."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from jarvis.config import NOTES_DIR, Config
from jarvis.memory import Memory
from tools.common import new_http_client
from tools.markets import MarketData, WatchItem, parse_watchlist
from tools.news import NewsData
from tools.pegs import PegData


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


@dataclass
class Services:
    cfg: Config
    http: httpx.AsyncClient
    markets: MarketData
    pegs: PegData
    news: NewsData
    notes_dir: Path

    @classmethod
    def create(cls, cfg: Config, memory: Memory | None, notes_dir: Path = NOTES_DIR) -> Services:
        http = new_http_client()
        return cls(
            cfg=cfg,
            http=http,
            markets=MarketData(http, os.environ.get("COINGECKO_API_KEY"), memory),
            pegs=PegData(http),
            news=NewsData(http, cfg.news_feeds),
            notes_dir=notes_dir,
        )

    def watchlist(self) -> list[WatchItem]:
        """Read fresh each time, so hand edits show up without a restart."""
        return parse_watchlist(read_text(self.cfg.watchlist_file))

    def watchlist_text(self) -> str:
        return read_text(self.cfg.watchlist_file)

    def theses_text(self) -> str:
        return read_text(self.cfg.theses_file)

    async def aclose(self) -> None:
        await self.http.aclose()
