"""Telling a concert from a show, and an opera from a metal band.

Every case here is a real listing from October 2026. Most of them are traps a
keyword match falls into.
"""

from __future__ import annotations

import pytest

from podiumwatch import music

CASES = [
    # The venue says what it is.
    ("parktheater", "Tosca", "Ukraine Chamber Opera", ["default", "opera"], music.OPERA),
    ("doelen", "Bach Collegium Japan", "", ["default", "klassiek", "jongerentarief", "orkest"], music.ORKEST),
    ("mge", "Ivan Petrenko", "Beethoven, Sjostakovitsj en Rachmaninov", ["Klassiek", "Piano"], music.KAMER),
    ("tilburg", "Philzuid", "Ravels Pianoconcert", ["klassieke muziek"], music.ORKEST),
    ("tilburg", "Alp Çelik", "Lunchconcert", ["klassieke muziek"], music.KAMER),
    ("tilburg", "Pynarello", "Pulcinella", ["klassieke muziek"], music.KLASSIEK),
    ("doelen", "Codarts Jazz & Global Sessions", "", ["default", "jazz", "global"], music.JAZZ),
    # Generic "muziek": the title narrows it, but only with strong words.
    ("tilburg", "Symfonisch Blaasorkest Drunen", "Musicals in Concert", ["muziek"], music.ORKEST),
    ("verkadefabriek", "Rembrandt Frerichs Kwartet", "", ["muziek"], music.LIVE),
    ("tilburg", "The Young Analogues", "Best of The Beatles", ["muziek"], music.LIVE),
    # Pop with an orchestra is still pop.
    ("mge", "Fresku & Metropole Orkest", "Hand op mijn hart", ["Pop", "Nederlandstalig"], music.LIVE),
    ("doelen", "Rock The Opera", "", ["default", "live in concert"], music.LIVE),
    # No genre at all: only an unmistakable title counts.
    ("chasse", "Storm - Music by Shabaka Hutchings", "OFFprojects en Jazz Orchestra", ["default"], music.JAZZ),
    ("chasse", "Elgars Celloconcert en Stravinsky's Vuurvogel", "", ["default"], music.KLASSIEK),
    ("chasse", "Between Dream and Dance", "Symfonisch Collectief", ["default"], music.ORKEST),
    ("chasse", "Het Trio", "Cabaret op zijn best", ["default"], None),
    # Traps.
    ("theaterrotterdam", "Reflections", "Nationale Opera & Ballet", ["default", "dans"], None),
    ("chasse", "Klassieker: De Tasjesdief (9+)", "Maria Peters", ["movie"], None),
    ("chasse", "Familieconcert Rhythmix", "Philzuid", ["default"], None),
    ("tilburg", "Op avontuur met Dikkie Dik (2+)", "Het Concertgebouw", ["jeugd & familie"], None),
    ("013", "Dark Oath", "Symfonische deathmetal", [], music.LIVE),
    ("013", "Geoff Tate's Operation: Mindcrime", "The Final Chapter", [], music.LIVE),
    ("paradox", "Hanneke's Klassieke Middag", "", ["chamber music"], music.KAMER),
    ("tilburg", "Martijn Koning", "", ["cabaret & comedy"], None),
]


@pytest.mark.parametrize("venue,title,subtitle,tags,expected", CASES,
                         ids=[c[1][:30] for c in CASES])
def test_kind(venue, title, subtitle, tags, expected):
    assert music.kind(venue, title, subtitle, tags) == expected


def test_unclear_classical_shows_under_both_orchestra_and_chamber():
    assert music.matches(music.KLASSIEK, [music.ORKEST])
    assert music.matches(music.KLASSIEK, [music.KAMER])
    assert not music.matches(music.KLASSIEK, [music.JAZZ])
    assert not music.matches(None, [music.OPERA])
    assert music.matches(None, [])


def test_opera_and_orchestra_weigh_most():
    assert music.WEIGHT[music.OPERA] == music.WEIGHT[music.ORKEST] == max(music.WEIGHT.values())
    assert music.WEIGHT[music.LIVE] == min(music.WEIGHT.values())
