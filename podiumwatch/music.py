"""Which performances are live music, and what kind.

Live music is what this tool is mostly for -- and classical music is where the
discounts are: MgE's Spottickets, de Doelen's youth rate, Philzuid's own youth
fare, and every Tilburg concert got the EUR 13,50 when its window opened.

The venue's own genre labels decide, because titles alone mislead badly. On
title words, Chasse's "Klassieker: De Tasjesdief (9+)" is a children's film,
013's "De Mis" and "Symfonische deathmetal" are metal, and Iron Maiden's
"Operation: Mindcrime" would be an opera. So keywords only *refine* a show the
venue already calls music, or decide at venues that give no genre at all --
and never at 013 or Paradox, whose whole programme is pop and jazz anyway.
"""

from __future__ import annotations

import re

from . import categories

OPERA = "opera"
ORKEST = "orkest"
KAMER = "kamermuziek"
# Classical, but the venue does not say which kind and the title gives no
# clue. Shown under both Orkest and Kamermuziek rather than guessed.
KLASSIEK = "klassiek"
JAZZ = "jazz"
LIVE = "live"

LABELS = {
    OPERA: "Opera",
    ORKEST: "Orkest",
    KAMER: "Kamermuziek",
    KLASSIEK: "Klassiek",
    JAZZ: "Jazz",
    LIVE: "Live muziek",
}

# The chips on the dashboard, and which kinds each one shows.
FILTERS = {
    OPERA: {OPERA},
    ORKEST: {ORKEST, KLASSIEK},
    KAMER: {KAMER, KLASSIEK},
    JAZZ: {JAZZ},
    LIVE: {LIVE},
}

# How much each kind lifts a show in "beste kansen". Opera and symphony
# orchestras are what matters most; everything musical gets something.
WEIGHT = {OPERA: 0.22, ORKEST: 0.22, KLASSIEK: 0.18, KAMER: 0.14, JAZZ: 0.08, LIVE: 0.05}

CLASSICAL = {OPERA, ORKEST, KAMER, KLASSIEK}

# Venues whose entire programme is one kind of music.
VENUE_KIND = {"013": LIVE, "paradox": JAZZ}

_OPERA_TAGS = {"opera", "operette"}
_ORKEST_TAGS = {"orkest", "orkestmuziek", "symfonisch"}
_KAMER_TAGS = {"strijkkwartet", "piano", "kamermuziek", "chamber music", "orgel",
               "koor", "lied", "vocaal", "barok", "oude muziek"}
_LIVE_TAGS = {"muziek", "pop", "rock", "singer-songwriter", "global", "nederlandstalig",
              "live in concert", "blues & americana", "soul & pop", "metal", "hiphop",
              "elektronisch", "wereldmuziek", "folk", "americana"}
# Genres that say "this is not a concert".
_NON_MUSIC_TAGS = {"dans", "dans modern", "theater", "toneel", "cabaret", "cabaret & comedy",
                   "musical", "musical & muziektheater", "muziektheater", "talk", "show",
                   "circus", "circus & variété", "performance", "expositie", "woord",
                   "theatercollege", "kennis & personality", "installatie", "special"}
# Explicitly not classical, so a title word cannot make it so: Fresku with the
# Metropole Orkest is a hip-hop show.
_NOT_CLASSICAL_TAGS = {"pop", "rock", "singer-songwriter", "hiphop", "metal",
                       "nederlandstalig", "elektronisch", "live in concert"}

_OPERA_RE = re.compile(
    r"opera\b|operette|\b(?:tosca|carmen|la traviata|la boh[eè]me|aida|rigoletto|"
    r"madama butterfly|zauberfl[oö]te|don giovanni|nozze di figaro|nabucco|turandot|"
    r"fidelio|eugene onegin|cosi fan tutte)\b",
    re.I,
)
_ORKEST_RE = re.compile(
    r"orkest|orchest|philharmon|filharmon|symfon|symphon|sinfonia|sinfonietta|"
    r"philzuid|camerata|symfonie",
    re.I,
)
_KAMER_RE = re.compile(
    r"kwartet|quartet|kamermuziek|recital|trio\b|sonate|sonata|lunchconcert|strijk|"
    r"pianist|cellist|violist|piano",
    re.I,
)
_COMPOSER_RE = re.compile(
    r"\b(?:bach|mozart|beethoven|brahms|mahler|bruckner|tsjaikovski|tchaikovsky|"
    r"stravinsk\w*|sjostakovitsj|shostakovich|schubert|schumann|haydn|h[aä]ndel|"
    r"debussy|ravel|rachmaninov|dvo[rř][aá]k|sibelius|vivaldi|elgar|chopin|liszt|"
    r"prokofjev|prokofiev|bart[oó]k|mendelssohn|requiem|matth[aä]us.?passion)\b",
    re.I,
)
_JAZZ_RE = re.compile(r"\bjazz", re.I)
# Chamber music words that stand on their own. "Kwartet" or "trio" do not:
# Rembrandt Frerichs Kwartet and Espen Eriksen Trio are jazz, so those only
# count once the venue has already said "klassiek".
_KAMER_STRONG_RE = re.compile(
    r"kamermuziek|recital|lunchconcert|strijkkwartet|string quartet|sonate|sonata",
    re.I,
)

# Children's shows and films are never what this filter is for.
_KIDS_TITLE_RE = re.compile(
    r"\(\s*\d{1,2}\s*\+\s*\)|\bklassieker:|familieconcert|familievoorstelling|kinderconcert",
    re.I,
)


def _norm(tags: list[str] | None) -> set[str]:
    return {(t or "").strip().lower() for t in tags or [] if t}


def kind(
    venue_id: str, title: str | None, subtitle: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """The kind of music a performance is, or None if it is not music."""
    t = _norm(tags)
    text = f"{title or ''} {subtitle or ''}"

    if t & (categories.KIDS | categories.FILM) or _KIDS_TITLE_RE.search(text):
        return None
    if categories.is_course(title, list(t)):
        return None

    if venue_id in VENUE_KIND:
        if venue_id == "paradox" and t & {"chamber music", "kamermuziek"}:
            return KAMER
        return VENUE_KIND[venue_id]

    # 1. The venue names it.
    if t & _OPERA_TAGS:
        return OPERA
    if t & _ORKEST_TAGS:
        return _OPERA_RE.search(text) and OPERA or ORKEST
    if t & _KAMER_TAGS:
        return KAMER
    if any(x.startswith("klassiek") for x in t):
        return _refine_classical(text, venue_says_classical=True) or KLASSIEK
    if any("jazz" in x for x in t):
        return JAZZ

    # 2. The venue calls it music without saying what kind.
    if t & _LIVE_TAGS:
        if not t & _NOT_CLASSICAL_TAGS:
            refined = _refine_classical(text)
            if refined:
                return refined
        return LIVE

    # 3. The venue gives a genre, and it is not music: "Reflections" by
    #    Nationale Opera & Ballet is a ballet, whatever its company is called.
    #    The one exception is music theatre that names itself an opera.
    if t & _NON_MUSIC_TAGS:
        if any("muziektheater" in x for x in t) and _OPERA_RE.search(text):
            return OPERA
        return None

    # 4. No genre at all (Chasse mostly). Only a title that is unmistakably
    #    musical counts; "trio" or "piano" in a cabaret title does not.
    return _refine_classical(text)


def _refine_classical(text: str, venue_says_classical: bool = False) -> str | None:
    """Narrow a show down by its title, strongest signal first."""
    # "OFFprojects en Jazz Orchestra" is jazz, orchestra or not -- unless the
    # venue itself filed it under classical.
    if not venue_says_classical and _JAZZ_RE.search(text):
        return JAZZ
    if _OPERA_RE.search(text):
        return OPERA
    if _ORKEST_RE.search(text):
        return ORKEST
    if _KAMER_STRONG_RE.search(text):
        return KAMER
    if venue_says_classical and _KAMER_RE.search(text):
        return KAMER
    if _COMPOSER_RE.search(text):
        return KLASSIEK
    return None


def matches(show_kind: str | None, wanted: list[str]) -> bool:
    """Does a show of `show_kind` pass the chosen filter chips?"""
    if not wanted:
        return True
    return any(show_kind in FILTERS.get(w, set()) for w in wanted)
