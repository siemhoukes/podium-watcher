"""What counts as "not for you", in one place.

The dashboard and the digest were filtering separately, and drifted: the
digest happily led with four free showings of a children's play and two
screenings of Minoes, because it only knew how to sort by price.

Kids' theatre is the big one -- a sizeable share of every venue's programme,
and none of it is the point. Film is hidden by default but genuinely wanted
sometimes, which is why it has its own toggle.
"""

from __future__ import annotations

# Parktheater says "familie & jeugd" where Tilburg says "jeugd & familie".
KIDS = {"jeugd & familie", "familie & jeugd", "kinderen", "kids", "familie"}
FILM = {"movie", "film"}
COURSES = {"cursus", "club", "workshop"}

# Hidden unless explicitly asked for, in every surface.
ALWAYS_HIDDEN = KIDS | COURSES


# Some venues tag a course by genre and only say "Cursus" in the title.
# MgE files "Cursus: Een goed gestemd gehoor" under Klassiek, so the tag
# filter never sees it. Deliberately narrow: "Masterclass" is left alone,
# because MgE's masterclasses are talk shows worth going to.
COURSE_TITLE_PREFIXES = ("cursus", "workshopdag", "workshop:")


def has_tag(tags: list[str] | None, wanted: set[str]) -> bool:
    return any((t or "").strip().lower() in wanted for t in (tags or []))


def is_course(title: str | None, tags: list[str] | None = None) -> bool:
    """A course or workshop rather than a performance."""
    if has_tag(tags, COURSES):
        return True
    low = (title or "").strip().lower()
    return low.startswith(COURSE_TITLE_PREFIXES)
