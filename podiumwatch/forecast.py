"""Will this still be gettable when the discount window opens?

Deliberately not a percentage. With a handful of observations per show, a
number like "82%" would be invented precision dressed up as a model, and the
whole point of storing history is to avoid exactly that. Instead this returns
a band plus how much evidence sits behind it, so a confident-looking row and
a guess never look the same.

The model gets better on its own as snapshots accumulate. Once Ticketmatic
seat counts land, `seats_free` turns the coarse bands into a real fill rate
and this module can be replaced with something quantitative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import clock
from .models import AVAILABLE, LIMITED, SOLD_OUT

# Bands, worst to best.
GONE = "gone"
TIGHT = "tight"
LIKELY = "likely"
UNKNOWN_OUTLOOK = "unknown"

# How much evidence is behind the band.
COLLECTING = "collecting"
LOW = "low"
MEDIUM = "medium"
HIGH = "high"


@dataclass
class Outlook:
    band: str
    confidence: str
    score: float  # 0..1, for ranking only
    reason: str
    observations: int = 0
    span_days: float = 0.0
    # Set only when a venue gave real numbers.
    fraction_sold: float | None = None
    seats_free: int | None = None
    seats_total: int | None = None
    sold_per_day: float | None = None


def _seat_outlook(
    snapshots: list[dict], starts_at: datetime | None, now: datetime
) -> Outlook | None:
    """Use real seat counts when we have them.

    A count is a fact, not a forecast, so even one reading says something a
    sold-out flag never could: '84% verkocht' is worth acting on. A *rate*
    still needs two readings, and only then is anything projected forward.
    """
    seated = [
        s for s in snapshots
        if s.get("seats_total") and s.get("seats_free") is not None
    ]
    if not seated:
        return None

    last = seated[-1]
    total = last["seats_total"]
    free = last["seats_free"]
    sold = 1.0 - (free / total)

    rate = None
    if len(seated) >= 2:
        first = seated[0]
        try:
            t0 = datetime.fromisoformat(first["observed_at"])
            t1 = datetime.fromisoformat(last["observed_at"])
            days = (t1 - t0).total_seconds() / 86400
        except (ValueError, TypeError):
            days = 0
        if days >= 0.25 and first["seats_total"]:
            sold0 = 1.0 - (first["seats_free"] / first["seats_total"])
            rate = (sold - sold0) / days

    # Project forward to curtain if we know how fast it is filling.
    projected = sold
    if rate is not None and rate > 0 and starts_at:
        days_left = max(0.0, (starts_at - now).total_seconds() / 86400)
        projected = min(1.0, sold + rate * days_left)

    pct = f"{sold * 100:.0f}%"
    if free <= 0:
        band, score, reason = GONE, 0.0, "Uitverkocht."
    elif projected >= 0.97 or free < 15:
        band, score = TIGHT, 0.2
        reason = f"{pct} verkocht, nog {free} van {total} plaatsen — krap."
    elif projected >= 0.85:
        band, score = TIGHT, 0.4
        reason = f"{pct} verkocht, nog {free} plaatsen."
    else:
        band = LIKELY
        score = 0.6 + 0.35 * (1.0 - projected)
        reason = f"{pct} verkocht, nog {free} van {total} plaatsen."

    if rate is not None:
        reason += f" Vult ~{rate * 100:.1f}%/dag."
        conf = HIGH if len(seated) >= 4 else MEDIUM
    else:
        conf = LOW  # a real number, but no trend yet

    return Outlook(
        band, conf, score, reason, len(snapshots),
        0.0, fraction_sold=sold, seats_free=free,
        seats_total=total, sold_per_day=rate,
    )


def _confidence(observations: int, span_days: float) -> str:
    if observations < 2:
        return COLLECTING
    if span_days < 3:
        return LOW
    if span_days < 10:
        return MEDIUM
    return HIGH


def outlook(
    snapshots: list[dict],
    starts_at: datetime | None = None,
    now: datetime | None = None,
) -> Outlook:
    """`snapshots` is oldest-first; each needs `state` and `observed_at`.

    `now` is passed in rather than read from the wall clock, so a forecast is
    reproducible: the old version quietly broke a test the day its fixed
    show date slid into the past.
    """
    now = now or clock.now()
    if not snapshots:
        return Outlook(UNKNOWN_OUTLOOK, COLLECTING, 0.5, "Nog niet waargenomen.")

    # Real numbers beat any inference from a coarse flag.
    seat_based = _seat_outlook(snapshots, starts_at, now)
    if seat_based is not None:
        return seat_based

    states = [s["state"] for s in snapshots]
    latest = states[-1]

    span_days = 0.0
    if len(snapshots) >= 2:
        try:
            first = datetime.fromisoformat(snapshots[0]["observed_at"])
            last = datetime.fromisoformat(snapshots[-1]["observed_at"])
            span_days = max(0.0, (last - first).total_seconds() / 86400)
        except (ValueError, TypeError):
            span_days = 0.0

    conf = _confidence(len(snapshots), span_days)

    # Sold out is observed fact, not a prediction.
    if latest == SOLD_OUT:
        return Outlook(
            GONE, HIGH, 0.0, "Uitverkocht — korting geldt hier niet.",
            len(snapshots), span_days,
        )

    if latest == LIMITED:
        return Outlook(
            TIGHT, max(conf, LOW, key=_rank), 0.25,
            "Laatste kaarten — kan snel weg zijn.",
            len(snapshots), span_days,
        )

    if latest != AVAILABLE:
        return Outlook(
            UNKNOWN_OUTLOOK, COLLECTING, 0.4,
            "Geen duidelijke verkoopstatus.",
            len(snapshots), span_days,
        )

    # Available. Did it get worse at any point? A show that has already
    # slipped from available to limited once is selling, even if it bounced
    # back, and that matters more than how long it has looked fine.
    degraded = any(
        _rank_state(states[i]) < _rank_state(states[i - 1])
        for i in range(1, len(states))
    )
    if degraded:
        return Outlook(
            TIGHT, conf, 0.45,
            "Beschikbaar, maar de verkoop trekt aan.",
            len(snapshots), span_days,
        )

    # Stable and available. Confidence rises with how long we have watched it
    # hold steady, which is exactly what we cannot know yet on day one.
    score = 0.70 + min(0.25, span_days / 40.0)
    if conf == COLLECTING:
        reason = "Beschikbaar — nog te weinig historie voor een voorspelling."
    elif conf == LOW:
        reason = f"Beschikbaar, stabiel over {span_days:.0f} dagen."
    else:
        reason = f"Stabiel beschikbaar over {span_days:.0f} dagen — ruime kans."
    return Outlook(LIKELY, conf, score, reason, len(snapshots), span_days)


_STATE_RANK = {SOLD_OUT: 0, LIMITED: 1, AVAILABLE: 2}
_CONF_RANK = {COLLECTING: 0, LOW: 1, MEDIUM: 2, HIGH: 3}


def _rank_state(state: str) -> int:
    return _STATE_RANK.get(state, 1)


def _rank(conf: str) -> int:
    return _CONF_RANK.get(conf, 0)
