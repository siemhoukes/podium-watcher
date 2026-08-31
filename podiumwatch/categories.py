"""What counts as "not for you", in one place.

The dashboard and the digest were filtering separately, and drifted: the
digest happily led with four free showings of a children's play and two
screenings of Minoes, because it only knew how to sort by price.

Kids' theatre is the big one -- a sizeable share of every venue's programme,
and none of it is the point. Film is hidden by default but genuinely wanted
sometimes, which is why it has its own toggle.
"""

from __future__ import annotations

KIDS = {"jeugd & familie", "kinderen", "kids", "familie"}
FILM = {"movie", "film"}
COURSES = {"cursus", "club", "workshop"}

# Hidden unless explicitly asked for, in every surface.
ALWAYS_HIDDEN = KIDS | COURSES


def has_tag(tags: list[str] | None, wanted: set[str]) -> bool:
    return any((t or "").strip().lower() in wanted for t in (tags or []))
