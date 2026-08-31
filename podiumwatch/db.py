"""SQLite storage: one row per show, one row per observation.

The snapshots table is append-only and is the whole point of the project --
a single reading tells you almost nothing, but the slope across two weeks
tells you whether a show will still be there when the discount window opens.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Show, Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
    id            INTEGER PRIMARY KEY,
    venue_id      TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    subtitle      TEXT,
    starts_at     TEXT,
    url           TEXT,
    hall          TEXT,
    tags          TEXT,
    ticket_url    TEXT,
    ticket_ref    TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    UNIQUE (venue_id, external_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id               INTEGER PRIMARY KEY,
    show_id          INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    observed_at      TEXT NOT NULL,
    state            TEXT NOT NULL,
    seats_free       INTEGER,
    seats_total      INTEGER,
    price_min_cents  INTEGER,
    price_max_cents  INTEGER,
    note             TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_show_time
    ON snapshots (show_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_shows_starts_at ON shows (starts_at);

-- Poll bookkeeping, so a silently-broken adapter is visible rather than
-- looking like a venue with nothing on.
CREATE TABLE IF NOT EXISTS poll_runs (
    id          INTEGER PRIMARY KEY,
    venue_id    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    shows_found INTEGER NOT NULL,
    error       TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_show(self, show: Show) -> int:
        now = utcnow()
        starts = show.starts_at.isoformat() if show.starts_at else None
        tags = json.dumps(show.tags, ensure_ascii=False) if show.tags else None
        cur = self.conn.execute(
            """
            INSERT INTO shows (venue_id, external_id, title, subtitle, starts_at,
                               url, hall, tags, ticket_url, ticket_ref,
                               first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (venue_id, external_id) DO UPDATE SET
                title      = excluded.title,
                subtitle   = excluded.subtitle,
                starts_at  = COALESCE(excluded.starts_at, shows.starts_at),
                url        = excluded.url,
                hall       = COALESCE(excluded.hall, shows.hall),
                tags       = COALESCE(excluded.tags, shows.tags),
                ticket_url = COALESCE(excluded.ticket_url, shows.ticket_url),
                ticket_ref = COALESCE(excluded.ticket_ref, shows.ticket_ref),
                last_seen  = excluded.last_seen
            """,
            (
                show.venue_id,
                show.external_id,
                show.title,
                show.subtitle,
                starts,
                show.url,
                show.hall,
                tags,
                show.ticket_url,
                show.ticket_ref,
                now,
                now,
            ),
        )
        if cur.lastrowid:
            row = self.conn.execute(
                "SELECT id FROM shows WHERE venue_id=? AND external_id=?",
                (show.venue_id, show.external_id),
            ).fetchone()
            return int(row["id"])
        return int(cur.lastrowid)

    def last_snapshot(self, show_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE show_id=? ORDER BY observed_at DESC LIMIT 1",
            (show_id,),
        ).fetchone()

    def add_snapshot(self, show_id: int, snap: Snapshot) -> bool:
        """Append an observation.

        Returns True if this reading differs from the previous one, which is
        what the change feed and the alerting care about.
        """
        prev = self.last_snapshot(show_id)
        changed = prev is None or (
            prev["state"] != snap.state
            or prev["seats_free"] != snap.seats_free
        )
        self.conn.execute(
            """
            INSERT INTO snapshots (show_id, observed_at, state, seats_free,
                                   seats_total, price_min_cents,
                                   price_max_cents, note)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                show_id,
                utcnow(),
                snap.state,
                snap.seats_free,
                snap.seats_total,
                snap.price_min_cents,
                snap.price_max_cents,
                snap.note,
            ),
        )
        return changed

    def record_run(
        self, venue_id: str, ok: bool, shows_found: int, error: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, error)"
            " VALUES (?,?,?,?,?)",
            (venue_id, utcnow(), 1 if ok else 0, shows_found, error),
        )

    def commit(self) -> None:
        self.conn.commit()

    def stats(self) -> dict:
        q = self.conn.execute
        return {
            "shows": q("SELECT COUNT(*) c FROM shows").fetchone()["c"],
            "snapshots": q("SELECT COUNT(*) c FROM snapshots").fetchone()["c"],
            "venues": q("SELECT COUNT(DISTINCT venue_id) c FROM shows").fetchone()["c"],
        }
