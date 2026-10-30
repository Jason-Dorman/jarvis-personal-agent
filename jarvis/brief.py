"""The morning brief (spec 8): one data call, and saving the spoken text.

`get_brief_data()` gathers everything in one round trip by reusing the other tools'
data code. Each section fails on its own, so one dead API doesn't sink the brief.

Run `uv run python -m jarvis.brief` to print a brief and save it without the HUD.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from jarvis.config import BRIEFS_DIR
from tools.common import describe_http_error, now_iso
from tools.markets import movers_from_snapshot
from tools.services import Services

TOP_HEADLINES = 5


async def _section(coro: Any) -> Any:
    try:
        return await coro
    except Exception as exc:
        return {"error": describe_http_error(exc)}


def assemble_brief(
    snapshot: dict[str, Any],
    threshold_pct: float,
    peg_alerts: dict[str, Any],
    peg_majors: Any,
    headlines: dict[str, Any],
    theses: str,
    today: date,
) -> dict[str, Any]:
    """Pure: shape the gathered pieces into the brief data the model reads."""
    errors: dict[str, str] = {}
    if "error" in snapshot:
        errors["watchlist"] = snapshot["error"]
        watchlist: dict[str, Any] = {"items": [], "movers": [], "quiet": []}
    else:
        movers = movers_from_snapshot(snapshot, threshold_pct)
        watchlist = {
            "items": snapshot["items"],
            "movers": movers["movers"],
            "quiet": movers["quiet"],
            "unavailable": [e["symbol"] for e in snapshot.get("errors", [])],
        }
    if "error" in peg_alerts:
        errors["pegs"] = peg_alerts["error"]
        pegs: dict[str, Any] = {"alerts": [], "all_normal": None, "majors": []}
    else:
        pegs = {
            "alerts": peg_alerts["alerts"],
            "all_normal": peg_alerts["all_normal"],
            "rules": peg_alerts.get("rules"),
            "majors": peg_majors if isinstance(peg_majors, list) else [],
        }
    if "error" in headlines:
        errors["headlines"] = headlines["error"]
        top = []
    else:
        top = headlines["headlines"][:TOP_HEADLINES]
    return {
        "date": today.isoformat(),
        "order": ["market mood", "watchlist movers", "pegs", "top 3 headlines", "theses"],
        "move_threshold_pct": threshold_pct,
        "watchlist": watchlist,
        "pegs": pegs,
        "headlines": top,
        "theses": theses,
        "errors": errors,
        "as_of": now_iso(),
    }


async def get_brief_data(svc: Services) -> dict[str, Any]:
    snapshot, alerts, majors, headlines = await asyncio.gather(
        _section(svc.markets.get_watchlist_snapshot(svc.watchlist())),
        _section(svc.pegs.list_peg_alerts()),
        _section(svc.pegs.majors()),
        _section(svc.news.get_headlines(hours=24, limit=TOP_HEADLINES)),
    )
    return assemble_brief(
        snapshot,
        svc.cfg.brief.move_threshold_pct,
        alerts,
        majors,
        headlines,
        svc.theses_text(),
        date.today(),
    )


def save_brief(text: str, day: date | None = None, briefs_dir: Path = BRIEFS_DIR) -> Path:
    """Write briefs/YYYY-MM-DD.md. Asking again the same day replaces the file."""
    day = day or date.today()
    briefs_dir.mkdir(parents=True, exist_ok=True)
    path = briefs_dir / f"{day.isoformat()}.md"
    path.write_text(f"# Morning brief, {day.isoformat()}\n\n{text.strip()}\n", encoding="utf-8")
    return path


async def _run() -> None:
    from jarvis.agent import Brain
    from jarvis.runtime import Runtime

    async with Runtime() as rt:
        brain = Brain(rt.cfg, rt.memory, rt.services)
        async with brain:
            async for chunk in brain.ask("Give me my morning brief."):
                print(chunk, end="", flush=True)
        print()
        if brain.last_brief_path:
            print(f"\nSaved to {brain.last_brief_path}")
        else:
            print("\nThe brief tool wasn't called, so nothing was saved.")


if __name__ == "__main__":
    asyncio.run(_run())
