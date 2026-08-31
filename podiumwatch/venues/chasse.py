"""Chasse Theater, Breda -- Peppered.

Deal: 50% off nearly all theatre performances, on the day of the show, with a
student card or CJP. Because it is a *percentage* rather than a flat fare, the
base price decides whether it beats EUR 10 elsewhere -- Chasse's own KOLJA at
EUR 34-56 is EUR 17-28 after the discount, i.e. worse than Tilburg's EUR 13,50.
So `price_min/max` matters more here than at any other venue.

Chasse is also a cinema. Films are kept (EUR 8 anyway, and Breda is 20 minutes
away) but tagged by production type so the dashboard can tell "a film" from
"an evening at the theatre".
"""

from .peppered import PepperedAdapter


class ChasseAdapter(PepperedAdapter):
    venue_id = "chasse"
    name = "Chassé Theater"
    city = "Breda"
    travel_minutes = 20
    base_url = "https://www.chasse.nl"
