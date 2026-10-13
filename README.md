# Podium Watcher

Tracks last-minute youth/student ticket deals at theatres near Tilburg, and
predicts **which shows will still be available when their discount window
opens**.

**Live at [podium.siem.codes](https://podium.siem.codes)**, behind the shared `siem-auth` gate.

## Why it forecasts instead of just checking

Every deal here only exists *inside* a window — 24 or 48 hours before the show,
or on the day itself. By the time the window opens the answer is already binary:
there is a ticket or there isn't. So checking "is there a cheap ticket right
now" is nearly useless.

The useful question is *"which of the next six weeks' shows will still be
under-sold when their window opens?"* — and that needs a **fill curve**. The
poller stores one observation per show per run; the slope across two weeks is
what predicts the outcome. A hall drifting slowly toward full is a safe bet; one
climbing fast is already gone.

Consequence: **this project is worth very little on day one and useful after a
month.** Collecting early matters more than any feature.

## The deals

Eligibility assumes a student card. Four of the five run off it rather than off
age, so only the MgE one expires at 30.

| Venue | City | Travel from Tilburg CS | Deal | Who | Window |
|---|---|---|---|---|---|
| de Doelen | Rotterdam | ~50 min | **€10** | t/m 30 | any time |
| Theater Rotterdam | Rotterdam | ~65 min | **€10** | t/m 30, **no card needed** | any time |
| Schouwburg & Concertzaal | Tilburg | 5 min walk | €13,50 incl. drink | t/m 26 **or student** | 48h, not sold out |
| Verkadefabriek | Den Bosch | ~25 min | **€10** (film €8) | student, t/m 29 | 24h |
| Muziekgebouw Eindhoven | Eindhoven | ~25 min | €10 Spotticket | under 30 | 24h, selected shows |
| Parktheater | Eindhoven | ~25 min + walk | €15 incl. drink | student / CJP | 24h |
| Chassé | Breda | ~20 min | **50% off** | student / CJP | day of show |

Tracked without a deal, because they are walkable and small enough to sell out:
**Paradox** (jazz, 175 cap) and **013**.

The three further-afield additions are the only ones whose discount is *not*
last-minute — all are bookable the moment tickets go on sale:

- **de Doelen** advertises €10 on every concert it sells itself. A third of
  its listing is `externe_verkoop` (Rotterdams Philharmonisch, Ticketmaster),
  where the rate cannot reach, and the box-office rule already excludes those.
- **Theater Rotterdam** is the only venue here that does not ask what card
  you hold: "Of je nu student bent, CJP-er, of al afgestudeerd: iedereen tot
  en met 30 jaar krijgt hetzelfde kortingstarief." Its agenda prints both a
  labelled fare table and a bare range beside it; reading the range made the
  €10 youth rate look like the door price, so the parser now prefers the
  table. See `_price_text`.
**TivoliVredenburg is built but switched off.** Its rate is the steepest
anywhere — "Rang 1 Regulier € 46,- Onder 30 jaar € 10,-" — and the adapter
works: 600 performances collected from a laptop. From the Pi, every request
comes back `403` with `cf-mitigated: challenge`, whatever user agent is used,
while de Doelen answers the same machine normally. That is Cloudflare bot
protection the venue deliberately switched on, and getting round it would mean
defeating an access control rather than reading a public page. So the adapter
and its tests stay, `ADAPTERS` does not include it, and its rows were deleted
rather than left to go stale.

Two things it did teach the rest of the project. Its rate is *selective* —
none of six sampled concerts carried the label — which is why selective deals
now have no `flat_cents` and are marked "niet op elk concert" in the UI,
making MgE's Spotticket honest too. And its listing has a date but no clock
time, which is why an unresolved show is stored with no date at all rather
than at midnight.

Deliberately excluded: **Theater aan de Parade** (Den Bosch) — its youth price
is age-only, t/m 25, with no student route, so it does not apply.

Note that Chassé's 50% is a *percentage*, so whether it beats a €10 flat fare
depends on the base price. That comparison is computed per show, not assumed.

## Availability: what each site actually gives us

Venues report at whatever depth their site allows and never pretend to more —
`seats_free` stays `NULL` rather than being guessed, because a fabricated number
would poison the forecast.

| Venue | Source | Platform | Signal | Rows |
|---|---|---|---|---|
| Chassé | `/programma?page=N` | Peppered | datetime, hall, 3-level status, labelled prices | 759 |
| Tilburg | `/nl/agenda?page=N` | Ticketmatic | **exact seat counts** + real prices | 514 |
| Parktheater | `/programma?page=N` | Peppered | datetime, hall, 3-level status, labelled prices | 381 |
| MgE | **`/event_feed_json/`** | Tixly | whole programme in one request, 6-level status | 224 |
| 013 | `/programma` | — | ISO datetimes + status ribbons | 161 |
| Verkadefabriek | `/podium?page=N` | **Ticketmatic** | 4-level + real prices, seats when seated | 90 |
| Paradox | `/agenda/` | Stager | schema.org microdata, prices, sold-out | 15 |

**Peppered is the good case — but it has two shapes, and only reading one of
them cost us every sold-out show.** An on-sale performance is an order anchor
with `data-event-id`, `data-event-start`, `data-event-hall` and a `status-*`
class. A sold-out one is a bare link to the production page with
`class="status-info status-uitverkocht"` and *no data attributes at all* — its
date, time and hall live on the card instead. Selecting only `a[data-event-id]`
therefore did not record those as sold out, it dropped them entirely: Chassé and
Parktheater both reported **zero** sold-out performances across 1,029 rows, and
a show that sold out simply vanished, ending its fill curve without capturing
the one event that matters most.

That also rules out `data-event-id` as identity, since it disappears exactly
when a show sells out. The key is production id + start time, observable in
both shapes.

**Verkadefabriek's listing is the weak case** — a show on sale has an *empty*
`.ticket-status` div and only a sold-out one has a badge, so absence-as-signal
breaks silently and the adapter warns if it finds events but no
`.ticket-status` elements at all. It matters less now: Verkadefabriek turns out
to run on Ticketmatic as well, and its `/agenda/koop-ticket?programId=` link
302s to the same signed widget, so it gets graded availability and real prices
from there instead.

**MgE was the blocked case, and the way in was not a scraper at all.** Its
agenda renders client-side, its 264 event links are hidden SEO placeholders,
`wp-json` answers 401, and `tickets.mge.nl/nl/events/` redirects to Tixly's
corporate site. Listing the live page's *own loaded resources* — rather than
guessing URLs — turned up `mge.nl/event_feed_json/`, which returns the entire
programme in one request with datetimes, halls, per-rank prices and a six-word
status vocabulary.

Its `tickets_free` / `tickets_total` are deliberately ignored: across the whole
feed not one record has them differ, eight sold-out concerts included. They are
static capacity, and using them would have printed a confident "0% sold" on a
sold-out Marillion.

## Exact seat counts — solved for Ticketmatic

Tilburg now yields a real fill rate. Two requests per performance:

```
GET <shows.ticket_url>          the signed widget URL the venue links to
    -> events[0].contingents[]              unseated: 4-level availability
    -> events[0].seatingplancontingents[]   seated: 4-level per rank
    -> events[0].prices.contingents[]       real prices, with conditions
    -> account.id

GET /widgets/backend/{account}/nl/eventseatingplan/{event.id}/availability/1
    -> {"sections":[{"rank":10000,"av":427,"tot":619}, ...]}
```

`av`/`tot` are free and total seats per rank. Summed, that is an exact fill
rate with a real denominator:

| | |
|---|---|
| Wibi Soerjadi | 564 / 766 free — **26% sold** |
| Pieter Derks | 142 / 886 free — **84% sold** |

Three things had to line up, and each looked like a dead end first:

- The **short** event id from the listing (`event=40274`) is rejected on its
  own. The widget only answers for the full signed URL carrying `accesskey`
  and `signature` — which was already sitting in `shows.ticket_url`.
- The `{code}` in the seating-plan path is not any numeric id but the event's
  own hashed `id` from the embedded JSON.
- Ticketmatic grades availability on a four-level scale — `good`, `reduced`,
  `last`, `none` — not a boolean, and `last` does occur in the wild.

This also solves Tilburg's missing prices: the widget carries the real fares
(`normaal € 24,50` beside a voucher-gated `ambassadeurs € 22,05`, which is
excluded because not everyone can buy it).

Unseated events ("vrije zit", "staanplaats", "vrije plaatskeuze") have no
seating plan, so they give the four-level word and no number. That is reported
as such.

**Verkadefabriek is on Ticketmatic too**, reached through its own
`/agenda/koop-ticket?programId=` redirect, so the identical reader serves both
venues. Its widget response is 5.7 MB where the event JSON ends by ~60 KB, so
the fetch is capped: `get_text(max_bytes=…)` streams and stops early rather
than pulling the bundle forty times a run.

**Peppered has no seat counts, and no seat map either.** This was first
written up as "the map sits behind a basket, so we won't hold seats to read
it". That was wrong, and worth correcting: there is nothing behind the basket.

Their whole site bundle (444 KB of JS) contains not one seat, seatingplan or
availability path. The cart payload is `event` + `priceIdAmount` — a price
category and a quantity, with **no seat identifier anywhere**. Price categories
are ranks ("1e rang", "volloopplaats"), not seats. Chassé and Parktheater sell
*n tickets in rank X* and assign places themselves.

So holding a seat would not reveal a number; it would return a basket. The
three-level status (`normaal` / `laatste_kaarten` / `uitverkocht`) is not a
limit we accepted out of caution — it is genuinely everything the system
tracks.

## Prices are labelled, and the labels matter

Peppered writes the price block two ways. Sometimes it is a bare range
(`€ 0,00–€ 13,00`) whose floor is a placeholder nobody pays. Sometimes it is
labelled categories:

```
volloopplaats normaal € 14,50   scholier/student/CJP € 10,00   Cineville € 4,50
```

Reading that as "minimum €4,50" and applying Chassé's 50% billed a €14,50 seat
as €2,25. Worse, taking the €10,00 as the base halved a fare **that was already
the student price**. So: `normaal` rows set the base price, a student/CJP row is
used *directly* as the fare, and subscription tiers (Cineville, BredaPas,
Kidspas) are ignored — they are not open to a walk-up buyer.

A published student category does **not** automatically win, though. Chassé's
`scholier/student/CJP € 10,00` is a real student fare, but Parktheater's CJP
category is only €2,50 off — €46,00 against a €48,50 normal price — because it
is their *advance* discount, not their €15 last-minute fare. Quoting it billed
Tosca at €46 for a show you can walk into for €15. Both options are published
and both are real, so the fare shown is simply the cheaper of the two, and the
label says which one it came from.

Verkadefabriek publishes no price in its listing at all, but its detail pages do
(`€ 26,00 Normaal`, `€ 24,00 Supporter`). The poller fetches those once per show,
capped per run, and never again — prices do not change.

**Tilburg publishes no price anywhere.** Not on the listing, not on the detail
page. Its fares live inside the Ticketmatic widget, behind the same hashed event
code that guards the seat counts. Until that is cracked, Tilburg shows its flat
€13,50 deal fare and an honest "normale prijs onbekend".

## Honesty about seat counts

`seats_free` is still `NULL` for every venue — nobody exposes a live seat count
in the listings yet. The dashboard therefore shows what was *observed* (the
venue's own sale status: in verkoop / laatste kaarten / uitverkocht) and renders
a seat line only when a real number exists. A trend sentence appears only once
there are at least two observations spanning a few days; below that it says
nothing rather than dressing one reading up as a forecast.

Sold-out performances are kept in the database — they are the most informative
thing that can happen to a show — and hidden from the list, since there is no
point offering a ticket that does not exist.

## Your own marks

Stars and hides live in the database, not in browser storage, because the
poller needs them: the window alert fires when a *starred* show's discount
window opens, and a browser cannot tell it that.

- **Starring is per performance** — you attend one night, not a run.
- **Hiding is per production** — a four-night run would otherwise need four
  clicks to go away. A starred performance stays visible even if its
  production is hidden.
- Both are keyed on `(venue_id, external_id)` / `(venue_id, title)` rather
  than `shows.id`. That internal id changes whenever a venue is cleared and
  re-collected, which has already happened twice, and would have silently
  wiped every favourite.

The toggles are plain POST forms, so they work without JavaScript and the
state survives a reload. The dashboard's read path stays read-only; only these
use a separate writable connection.

## Alerts

The dashboard answers *what is worth planning around*. An alert answers *you
can buy it now*, which is true for a few hours and cannot be noticed on a page
you are not looking at — so alerting runs in the poller, not the web app.

**Everything goes by email.** ntfy pushes went unread; email is what makes the
phone buzz. ntfy is still there as a fallback for a box with no mail set up.

- **Window open** — only for **starred** performances. A message every time a
  Tilburg show entered its 48 hours would be several a day, which is how a
  quiet tool becomes one you mute. Several windows opening in one poll arrive
  as one mail. `alerts_sent` records each (performance, kind) pair, so a
  window that stays open across four polls mails once; sold-out shows and
  shows the venue no longer lists are skipped.
- **Weekly digest** — Monday mornings, far enough ahead to plan a weekend
  around. It applies the same category filters as the dashboard, which it did
  not at first: sorting by price alone put four free showings of a children's
  play and two screenings of Minoes at the top. Those filters live in
  `categories.py` so the two surfaces cannot drift again.
- **A scraper is broken** — once a venue has been failing or incomplete for
  12 hours, and once more when it recovers. One failed poll is usually the
  network (all nine venues failed together on 17 September), so a single
  blip never mails.

Configured in `/etc/podium-watcher/podium.env`; with no mail settings the
poller simply collects:

```bash
PODIUM_SMTP_HOST=smtp.gmail.com
PODIUM_SMTP_PORT=587
PODIUM_SMTP_USER=...
PODIUM_SMTP_PASS=...        # an app password, not the account password
PODIUM_MAIL_TO=...
```

## When a scraper breaks

Scrapers fail silently, and in late September 2026 three of them did at once:

- **Peppered renamed its page parameter** from `page` to `p54_page`. Every
  hand-built `?page=2` then returned page one, the loop saw only repeats and
  stopped, and Chassé, Parktheater, de Doelen and Theater Rotterdam lost
  85–95% of their programme.
- **Verkadefabriek turned `/podium` into a six-show teaser**, with a button to
  the real listing at `/agenda?genre=…`. Paging through the teaser skipped
  about half of all performances.
- **Paradox keyed shows on the ticket-button id**, which disappears when a
  show sells out — so a selling-out show split in two.

Every run still logged as a clean success, and `/status` — comparing each
venue with its own last ten runs — called the collapse normal after three
days. Nothing was wrong for three weeks as far as the tool could tell. What
changed:

- **Pages are walked by their own `rel="next"` link** (`VenueAdapter.paginate`),
  never by a URL we build, and the walk is strict: page one failing or empty
  raises; a later page failing, a page that only repeats earlier shows, or a
  listing longer than the page cap is recorded as an *incomplete* run.
- **Waiting rooms and bot checks are recognised** (Parktheater's virtual
  queue, BunnyCDN's JavaScript check) and logged as failures with that reason.
  They are detected, never worked around: that is the venue saying "not now",
  and the next run tries again.
- **Health compares against four weeks** (75th percentile), so a collapse has
  to last three weeks before it looks normal, and separately counts upcoming
  shows that the latest complete run no longer lists.
- **The dashboard stops presenting stale data as current.** A show the venue's
  latest complete listing no longer has is hidden (cancelled, moved, off
  sale); a show whose venue has not been read for 30 hours carries "stand van
  …" next to its status.
- **And it mails** — see *Alerts* above.

All times are Europe/Amsterdam (`clock.py`). The Pi runs on Europe/London, so
reading its wall clock opened every 24-hour window an hour late.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

168 tests, many over gzipped copies of the real pages. Scrapers fail *silently* -- a venue redesigns, a selector stops
matching, and the poller records "nothing on" for a fortnight -- so the
adapter tests assert on values read off those fixtures and break loudly
instead.

Every bug this project has had is pinned as a test: sold-out performances
being dropped rather than recorded, ids that changed when a show sold out,
Cineville subscription prices masquerading as the base fare, a CJP advance
discount masquerading as a student fare, a "discount" dearer than the door
price, and undated January shows landing eight months in the past.

The suite earned its keep immediately: it caught that the MgE adapter was
discarding 40 of 264 performances -- every free event and every third-party
sale, all of which carry `event_id: false`.

## Running it

```bash
pip install -r requirements.txt
python run_once.py               # poll every venue once
python run_once.py --venue tilburg -v
```

The database lands in `data/podium.sqlite3`. `snapshots` is append-only.

## Layout

```
podiumwatch/
  http.py            polite session: real UA, retries, jitter
  models.py          Show / Snapshot, and the availability states
  db.py              SQLite schema + upserts
  poller.py          runs every adapter, isolates failures
  venues/
    base.py          adapter contract + Dutch date parsing
    peppered.py      shared parser for Peppered venues
    chasse.py        Chassé Breda        (Peppered)
    parktheater.py   Parktheater         (Peppered)
    tilburg.py       Schouwburg & Concertzaal Tilburg
    verkadefabriek.py
  deals.py           what you actually pay, and when each window opens
  forecast.py        will it still be gettable? bands, not fake percentages
  web.py             read-only Flask dashboard
templates/           paper-programme UI
run_once.py          poll once
run_web.py           local dev server
deploy/              installer + nginx site
services/            unit + timer, version-controlled
```

One broken venue never stops the others — failures are recorded in `poll_runs`
so a silently-broken adapter looks different from a venue with nothing on.

### A performance is the unit, not a production

A production runs for several nights; each night sells out separately. So one
row is one *performance*.

This matters more than it sounds. Verkadefabriek's listing repeats a production
once per date but the link only carries the **production** id — keying on that
alone collapsed a four-night run into a single row that collected four snapshots
per poll, which destroys the very curve the project rests on. Its external id is
therefore production id + start time. Peppered venues give a real per-performance
id and need no such trick.

## Deployment

Two units on the Pi: `podium-poller.timer` polls four times a day (06/12/18/22,
15-minute random delay, `Persistent=true` so a missed run still collects), and
`podium-web.service` serves the dashboard under gunicorn on port 5011. nginx
fronts it at `podium.siem.codes` behind `siem-auth`.

Adding the subdomain to the tunnel has one trap: the service runs with
`--config /etc/cloudflared/config.yml`, but a bare `cloudflared tunnel ingress
validate` reads `~/.cloudflared/config.yml`, which is a different, stale file.
Always pass `--config` explicitly. `route dns` must run as `sausje` *without*
sudo, since `cert.pem` lives in that home directory.

```bash
ssh sausje@raspberrypi "cd ~/podium-watcher && git pull --ff-only && sudo systemctl start podium-poller.service"
systemctl list-timers podium-poller.timer --no-pager
journalctl -u podium-poller -n 50 --no-pager
```

`.gitattributes` forces LF: the repo is edited on Windows and run on Linux, and
a CRLF `install.sh` fails with "bad interpreter".

## Still to build

- Hall capacities for the Peppered venues, so a 3-level status can at least
  be read against the size of the room
- A real forecast once the curves have history — `forecast.py` still returns
  a band plus an observation count, not a percentage, on purpose
- One-tap *got it / missed it* logging, to learn whether venues cap the youth
  allocation separately from the hall

