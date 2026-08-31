"""Run every venue adapter once and append what it saw to the database."""

from __future__ import annotations

import logging
from pathlib import Path

from .db import Database
from .http import Fetcher
from .venues import ADAPTERS

log = logging.getLogger(__name__)

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "podium.sqlite3"


def poll_once(db_path: str | Path = DEFAULT_DB, only: str | None = None) -> dict:
    db = Database(db_path)
    fetcher = Fetcher()
    summary = {"venues": {}, "changes": 0, "shows": 0}

    try:
        for adapter_cls in ADAPTERS:
            if only and adapter_cls.venue_id != only:
                continue
            adapter = adapter_cls(fetcher)
            try:
                pairs = adapter.fetch()
            except Exception as exc:  # one broken venue must not stop the rest
                log.exception("%s: adapter failed", adapter.venue_id)
                db.record_run(adapter.venue_id, ok=False, shows_found=0, error=str(exc))
                summary["venues"][adapter.venue_id] = {"error": str(exc)}
                continue

            changes = 0
            for show, snap in pairs:
                show_id = db.upsert_show(show)
                if db.add_snapshot(show_id, snap):
                    changes += 1

            db.record_run(adapter.venue_id, ok=bool(pairs), shows_found=len(pairs))
            db.commit()

            summary["venues"][adapter.venue_id] = {
                "shows": len(pairs),
                "changes": changes,
            }
            summary["shows"] += len(pairs)
            summary["changes"] += changes
            log.info(
                "%s: %s shows, %s changed", adapter.venue_id, len(pairs), changes
            )
    finally:
        db.commit()
        summary["totals"] = db.stats()
        db.close()

    return summary
