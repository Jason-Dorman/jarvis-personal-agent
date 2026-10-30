"""Startup shared by every entry point: .env, config, the database, and data services."""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv

from jarvis.config import DB_PATH, LOGS_DIR, ROOT, Config, get_config
from jarvis.memory import Memory
from tools.services import Services


def setup_logging(debug: bool) -> None:
    """Local log file only (spec 9). The terminal stays clean for the conversation."""
    LOGS_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOGS_DIR / "jarvis.log", encoding="utf-8")],
    )
    for noisy in ("httpx", "httpcore", "yfinance", "peewee", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Runtime:
    """`async with Runtime() as rt:` gives you rt.cfg, rt.memory, rt.services."""

    def __init__(self) -> None:
        load_dotenv(ROOT / ".env")
        self.cfg: Config = get_config()
        setup_logging(self.cfg.debug)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("Warning: ANTHROPIC_API_KEY is not set in .env. The brain can't answer.")
        self.memory = Memory(DB_PATH)
        self.services = Services.create(self.cfg, self.memory)

    async def __aenter__(self) -> Runtime:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.services.aclose()
        self.memory.close()
