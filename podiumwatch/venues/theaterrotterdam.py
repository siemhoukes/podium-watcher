"""Theater Rotterdam -- Peppered.

Deal: **EUR 10 for everyone up to and including 30, on every performance.**
Their own wording is unusually blunt about it: "Of je nu student bent, CJP-er,
of al afgestudeerd: bij Theater Rotterdam krijgt iedereen tot en met 30 jaar
hetzelfde kortingstarief." No student card needed, no last-minute window --
which makes it the only venue here you can plan around months ahead.

Sixty-five minutes from Tilburg CS, the far edge of what is worth an evening,
but the fare is the lowest on the board and de Doelen already puts Rotterdam
in range.

Same Peppered markup as Chasse, Parktheater and de Doelen, so the shared
parser handles it unchanged. As there, that means three-level status and no
seat counts: Peppered publishes no seat map at all.
"""

from .peppered import PepperedAdapter


class TheaterRotterdamAdapter(PepperedAdapter):
    venue_id = "theaterrotterdam"
    name = "Theater Rotterdam"
    city = "Rotterdam"
    travel_minutes = 65
    base_url = "https://www.theaterrotterdam.nl"
    programme_path = "/agenda"
