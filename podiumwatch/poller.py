"""Run every venue adapter once and append what it saw to the database."""

from __future__ import annotations

import logging
from pathlib import Path

from .db import Database
from .http import Fetcher
from .models import Snapshot, Show
from .venues import ADAPTERS

log = logging.getLogger(__name__)

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "podium.sqlite3"

# Detail pages are one request each, so the first run on a venue is spread
# over several polls rather than hammering the site in one go. Prices do not
# change, so once a show has one it is never fetched again.
PRICE_BUDGET_PER_RUN = 40


def _enrich_prices(db: Database, adapter, pairs: list[tuple[Show, Snapshot]]) -> int:
    """Fill in prices the listing page does not carry.

    Only for adapters that opt in with `fetch_detail_price`, only for shows
    we have never priced, and only up to a per-run budget.
    """
    fetch_price = getattr(adapter, "fetch_detail_price", None)
    if fetch_price is None:
        return 0

    spent = 0
    for show, snap in pairs:
        if snap.price_min_cents is not None:
            continue
        cached = db.known_price(show.venue_id, show.external_id)
        if cached is not None:
            snap.price_min_cents, snap.price_max_cents = cached
            continue
        if spent >= PRICE_BUDGET_PER_RUN:
            continue
        spent += 1
        try:
            lo, hi = fetch_price(show)
        except Exception:
            log.exception("%s: price fetch failed for %s", adapter.venue_id, show.url)
            continue
        if lo is not None:
            snap.price_min_cents, snap.price_max_cents = lo, hi
    if spent:
        log.info("%s: fetched %s detail prices this run", adapter.venue_id, spent)
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

            priced = _enrich_prices(db, adapter, pairs)

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
                "prices_fetched": priced,
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
