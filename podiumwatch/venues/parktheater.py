"""Parktheater Eindhoven -- Peppered, same markup as Chasse.

Deal: EUR 15 including a drink for students / CJP, online from 24h before,
on almost anything not sold out. Productions with a base price of EUR 50+
are EUR 25 instead.

Caveat worth remembering: the venue sits in the Stadswandelpark, a further
15-20 minute walk (or a bus) from Eindhoven Centraal, so it is the least
convenient of the five despite Eindhoven being close.
"""

from .peppered import PepperedAdapter


class ParktheaterAdapter(PepperedAdapter):
    venue_id = "parktheater"
    name = "Parktheater Eindhoven"
    city = "Eindhoven"
    travel_minutes = 25
    base_url = "https://www.parktheater.nl"
