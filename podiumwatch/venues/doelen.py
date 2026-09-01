"""de Doelen, Rotterdam -- Peppered.

Deal: **EUR 10 for anyone up to and including 30, on every concert.** Not a
last-minute gamble like the rest of this list; you can book it in advance. The
catch is that only a limited number of places carry that rate, so it can run
out long before the hall does -- which is exactly the kind of thing worth
watching over time.

Fifty minutes from Tilburg CS, so a real evening out rather than a walk. Worth
it for the programme: this is the Rotterdam classical hall.

A third of its listing is `status-externe_verkoop`, sold by the Rotterdams
Philharmonisch or Ticketmaster rather than the hall. The youth rate cannot
reach those, and the box-office rule already excludes them.

Its ticket buttons point at Ticketmatic in one place -- a waiting-list widget
-- but its own sales run through Peppered's `/nl/order/add/event/`, so there
are no seat counts here, only the three-level status.
"""

from .peppered import PepperedAdapter


class DoelenAdapter(PepperedAdapter):
    venue_id = "doelen"
    name = "de Doelen"
    city = "Rotterdam"
    travel_minutes = 50
    base_url = "https://www.dedoelen.nl"
    programme_path = "/nl/agenda"
