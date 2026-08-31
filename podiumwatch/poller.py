"""Run every venue adapter once and append what it saw to the database."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .db import Database
from .http import Fetcher
from .models import Snapshot, Show
from .venues import ADAPTERS

log = logging.getLogger(__name__)

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "podium.sqlite3"

def _enrich(db: Database, adapter, pairs: list[tuple[Show, Snapshot]]) -> int:
    """Spend the adapter's per-run request budget on per-show detail.

    Soonest first. A seat count three days out is worth reading every run; one
    for next April is trivia, and the budget exists precisely because there is
    not enough of it to go round. Shows already past are skipped outright.
    """
    budget = getattr(adapter, "enrich_budget", 0)
    if not budget:
        return 0

    now = datetime.now()
    upcoming = [
        (show, snap)
        for show, snap in pairs
        if show.starts_at is None or show.starts_at >= now
    ]
    upcoming.sort(key=lambda p: p[0].starts_at or datetime.max)

    spent = 0
    for show, snap in upcoming:
        cached = db.known_price(show.venue_id, show.external_id)

        # Applying a price we already know costs nothing, so it happens for
        # every show regardless of budget. Breaking out of the loop instead
        # dropped Verkadefabriek from 85 priced shows to 40 -- the cache was
        # never reached for anything past the budget.
        if snap.price_min_cents is None and cached is not None:
            snap.price_min_cents, snap.price_max_cents = cached

        if spent >= budget:
            continue
        try:
            if adapter.enrich(show, snap, cached_price=cached):
                spent += 1
        except Exception:
            log.exception("%s: enrich failed for %s", adapter.venue_id, show.url)
    if spent:
        log.info("%s: enriched %s shows this run", adapter.venue_id, spent)
    return spent


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

            priced = _enrich(db, adapter, pairs)

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
                "enriched": priced,
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
