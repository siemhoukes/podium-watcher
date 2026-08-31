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


def _confidence(observations: int, span_days: float) -> str:
    if observations < 2:
        return COLLECTING
    if span_days < 3:
        return LOW
    if span_days < 10:
        return MEDIUM
    return HIGH


def outlook(snapshots: list[dict], starts_at: datetime | None = None) -> Outlook:
    """`snapshots` is oldest-first; each needs `state` and `observed_at`."""
    if not snapshots:
        return Outlook(UNKNOWN_OUTLOOK, COLLECTING, 0.5, "Nog niet waargenomen.")

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
