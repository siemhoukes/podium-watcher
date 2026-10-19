"""Run every venue adapter once and append what it saw to the database."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from . import clock, notify
from .db import Database, utcnow
from .http import Fetcher
from .models import SOLD_ELSEWHERE, Snapshot, Show
from .venues import ADAPTERS
from .venues.base import FetchError

log = logging.getLogger(__name__)

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "podium.sqlite3"

# Share of the per-run budget reserved for shows never enriched before.
BACKLOG_SHARE = 0.25


def _enrich(db: Database, adapter, pairs: list[tuple[Show, Snapshot]]) -> int:
    """Spend the adapter's per-run request budget on per-show detail.

    Two things happen here, and only one of them costs a request.

    Reusing a price we already know is free, so that happens for *every* show.
    Breaking out of the loop once the budget ran out dropped Verkadefabriek
    from 85 priced shows to 40 between two runs, because everything past the
    budget never reached the cache.

    Fetching is rationed, and split. Soonest-first alone starved the far end
    of the calendar: 514 Tilburg shows against 60 fetches a run meant the same
    near dates were re-read four times a day while anything past a few weeks
    never got a price at all. Most of the budget still goes to the near end,
    where the fill curve is worth watching, and a quarter is reserved for
    shows never enriched before, which fills the rest of the season in over a
    few days.
    """
    budget = getattr(adapter, "enrich_budget", 0)
    if not budget:
        return 0

    now = clock.now()
    upcoming = [
        (show, snap)
        for show, snap in pairs
        if show.starts_at is None or show.starts_at >= now
    ]
    # Shows with no start time yet sort first. TivoliVredenburg's listing
    # carries a date but no clock time, so a show stays dateless -- and
    # therefore invisible on the dashboard -- until its detail page is read.
    # Sorting them last would have meant they never got budget at all.
    upcoming.sort(key=lambda p: p[0].starts_at or datetime.min)

    # Anything already known and expensive to re-fetch goes in before the
    # budget is spent. Hall plans are ~450 KB and never change.
    if hasattr(adapter, "_capacity_cache"):
        adapter._capacity_cache = {
            show.external_id: cap
            for show, _ in upcoming
            if (cap := db.known_capacity(show.venue_id, show.external_id))
        }

    cached_for: dict[tuple[str, str], tuple | None] = {}
    elsewhere: set[tuple[str, str]] = set()
    for show, snap in upcoming:
        key = (show.venue_id, show.external_id)
        if db.known_sold_elsewhere(*key):
            # Keep saying so between box-office readings.
            elsewhere.add(key)
            snap.note = SOLD_ELSEWHERE
            cached_for[key] = None
            continue
        cached = db.known_price(*key)
        cached_for[key] = cached
        if snap.price_min_cents is None and cached is not None:
            snap.price_min_cents, snap.price_max_cents = cached

    never_priced = [
        p for p in upcoming
        if cached_for[(p[0].venue_id, p[0].external_id)] is None
        and (p[0].venue_id, p[0].external_id) not in elsewhere
    ]
    reserved = min(len(never_priced), int(budget * BACKLOG_SHARE))
    # Take the backlog from the far end, since the near end is covered anyway.
    backlog = never_priced[-reserved:] if reserved else []
    backlog_keys = {(s.venue_id, s.external_id) for s, _ in backlog}

    soonest = [
        p for p in upcoming
        if (p[0].venue_id, p[0].external_id) not in backlog_keys
    ][: budget - reserved]

    spent = 0
    for show, snap in soonest + backlog:
        if spent >= budget:
            break
        try:
            if adapter.enrich(
                show, snap,
                cached_price=cached_for[(show.venue_id, show.external_id)],
            ):
                spent += 1
        except Exception:
            log.exception("%s: enrich failed for %s", adapter.venue_id, show.url)

    if spent:
        log.info(
            "%s: enriched %s shows this run (%s from the backlog)",
            adapter.venue_id, spent, len(backlog),
        )
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
            started = utcnow()
            try:
                pairs = adapter.fetch()
            except FetchError as exc:
                # Expected kinds of failure -- a waiting room, a page that
                # would not load. A reason is enough; a traceback is noise.
                log.warning("%s: %s", adapter.venue_id, exc)
                db.record_run(
                    adapter.venue_id, ok=False, shows_found=0, error=str(exc),
                    started_at=started,
                )
                summary["venues"][adapter.venue_id] = {"error": str(exc)}
                continue
            except Exception as exc:  # one broken venue must not stop the rest
                log.exception("%s: adapter failed", adapter.venue_id)
                db.record_run(
                    adapter.venue_id, ok=False, shows_found=0, error=str(exc),
                    started_at=started,
                )
                summary["venues"][adapter.venue_id] = {"error": str(exc)}
                continue

            enriched = _enrich(db, adapter, pairs)

            changes = 0
            for show, snap in pairs:
                show_id = db.upsert_show(show)
                if db.add_snapshot(show_id, snap):
                    changes += 1

            # An empty run used to be recorded as failed with no reason at all,
            # and a run cut short halfway as a clean success. Both now say why.
            problems = "; ".join(adapter.problems) or None
            if pairs:
                db.record_run(
                    adapter.venue_id, ok=True, shows_found=len(pairs), error=problems,
                    started_at=started, complete=not problems,
                )
            else:
                db.record_run(
                    adapter.venue_id, ok=False, shows_found=0,
                    error=problems or "Geen voorstellingen gevonden",
                    started_at=started,
                )
            if problems:
                log.warning("%s: incomplete run: %s", adapter.venue_id, problems)
            db.commit()

            summary["venues"][adapter.venue_id] = {
                "shows": len(pairs),
                "changes": changes,
                "enriched": enriched,
            }
            summary["shows"] += len(pairs)
            summary["changes"] += changes
            log.info(
                "%s: %s shows, %s changed", adapter.venue_id, len(pairs), changes
            )

        # Alerting lives here, not in the dashboard: a page nobody has open
        # cannot notice that a window just opened.
        try:
            summary["alerts"] = notify.send_window_alerts(db.conn)
        except Exception:
            log.exception("alerting failed")
            summary["alerts"] = 0
        # Only on a full poll: a single-venue debug run says nothing about
        # whether the others are healthy, and should not mail concerts.
        if not only:
            try:
                summary["music_alerts"] = notify.send_music_alerts(db.conn)
            except Exception:
                log.exception("concert alerting failed")
            try:
                summary["health_mails"] = notify.send_health_alerts(
                    db.conn, [a.venue_id for a in ADAPTERS]
                )
            except Exception:
                log.exception("health alerting failed")
    finally:
        db.commit()
        summary["totals"] = db.stats()
        db.close()

    return summary
