"""The outlook: what it says, and what it refuses to say.

The rule this file mostly guards is negative. With one coarse reading and no
history, the honest output is "not enough to say" -- not a percentage that
looks like a model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from podiumwatch import forecast
from podiumwatch.models import AVAILABLE, LIMITED, SOLD_OUT

NOW = datetime(2026, 9, 1, 12, 0)


def snap(state=AVAILABLE, free=None, total=None, days_ago=0.0):
    return {
        "state": state,
        "seats_free": free,
        "seats_total": total,
        "observed_at": (NOW - timedelta(days=days_ago)).isoformat(),
    }


# --------------------------------------------------------------------------
# Without seat numbers
# --------------------------------------------------------------------------

def test_one_coarse_reading_makes_no_forecast():
    out = forecast.outlook([snap()], NOW + timedelta(days=20))
    assert out.confidence == forecast.COLLECTING
    assert out.fraction_sold is None
    assert out.sold_per_day is None


def test_sold_out_is_observed_not_predicted():
    out = forecast.outlook([snap(state=SOLD_OUT)], NOW + timedelta(days=20))
    assert out.band == forecast.GONE
    assert out.confidence == forecast.HIGH
    assert out.score == 0.0


def test_a_show_that_slipped_to_limited_reads_as_tight():
    """Selling is worse news than having always looked fine."""
    history = [snap(days_ago=8), snap(state=LIMITED, days_ago=1)]
    out = forecast.outlook(history, NOW + timedelta(days=10))
    assert out.band == forecast.TIGHT


def test_confidence_grows_with_the_span_watched():
    short = forecast.outlook([snap(days_ago=1), snap()], NOW + timedelta(days=30))
    long = forecast.outlook([snap(days_ago=20), snap()], NOW + timedelta(days=30))
    assert short.confidence == forecast.LOW
    assert long.confidence == forecast.HIGH


# --------------------------------------------------------------------------
# With real seat counts
# --------------------------------------------------------------------------

def test_a_single_seat_count_states_a_fact():
    """One count is not a forecast, but 84% sold is worth knowing."""
    out = forecast.outlook([snap(free=142, total=886)], NOW + timedelta(days=20))
    assert out.seats_free == 142
    assert round(out.fraction_sold, 2) == 0.84
    assert out.sold_per_day is None, "a rate needs two readings"
    assert out.confidence == forecast.LOW


def test_seat_counts_beat_the_coarse_band():
    """A venue saying 'available' while 2 seats remain is still tight."""
    out = forecast.outlook(
        [snap(state=AVAILABLE, free=2, total=886)], NOW + timedelta(days=5)
    )
    assert out.band == forecast.TIGHT


def test_a_fast_filling_hall_is_projected_forward():
    history = [
        snap(free=500, total=766, days_ago=6),
        snap(free=300, total=766, days_ago=0),
    ]
    out = forecast.outlook(history, NOW + timedelta(days=20))
    assert out.sold_per_day is not None and out.sold_per_day > 0.03
    assert out.band == forecast.TIGHT, "at this rate it is gone before curtain"


def test_a_slow_filling_hall_stays_likely():
    history = [
        snap(free=560, total=766, days_ago=6),
        snap(free=540, total=766, days_ago=0),
    ]
    out = forecast.outlook(history, NOW + timedelta(days=20))
    assert out.band == forecast.LIKELY


def test_zero_seats_free_is_gone_whatever_the_flag_said():
    out = forecast.outlook(
        [snap(state=AVAILABLE, free=0, total=665)], NOW + timedelta(days=20)
    )
    assert out.band == forecast.GONE


def test_emptier_halls_rank_above_fuller_ones():
    empty = forecast.outlook([snap(free=700, total=766)], NOW + timedelta(days=20))
    full = forecast.outlook([snap(free=60, total=766)], NOW + timedelta(days=20))
    assert empty.score > full.score


def test_no_history_at_all():
    out = forecast.outlook([], NOW)
    assert out.band == forecast.UNKNOWN_OUTLOOK
    assert out.confidence == forecast.COLLECTING
