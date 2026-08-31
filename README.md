# Podium Watcher

Tracks last-minute youth/student ticket deals at theatres near Tilburg, and
predicts **which shows will still be available when their discount window
opens**.

Destined for `podium.siem.codes` on the Pi, behind the shared `siem-auth` gate.

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
| Schouwburg & Concertzaal | Tilburg | 5 min walk | €13,50 incl. drink | t/m 26 **or student** | 48h, not sold out |
| Verkadefabriek | Den Bosch | ~25 min | **€10** (film €8) | student, t/m 29 | 24h |
| Muziekgebouw Eindhoven | Eindhoven | ~25 min | €10 Spotticket | under 30 | 24h, selected shows |
| Parktheater | Eindhoven | ~25 min + walk | €15 incl. drink | student / CJP | 24h |
| Chassé | Breda | ~20 min | **50% off** | student / CJP | day of show |

Tracked without a deal, because they are walkable and small enough to sell out:
**Paradox** (jazz, 175 cap) and **013**.

Deliberately excluded: **Theater aan de Parade** (Den Bosch) — its youth price
is age-only, t/m 25, with no student route, so it does not apply.

Note that Chassé's 50% is a *percentage*, so whether it beats a €10 flat fare
depends on the base price. That comparison is computed per show, not assumed.

## Availability: what each site actually gives us

Venues report at whatever depth their site allows and never pretend to more —
`seats_free` stays `NULL` rather than being guessed, because a fabricated number
would poison the forecast.

| Venue | Agenda | Platform | Signal | Status |
|---|---|---|---|---|
| Chassé | `/programma?page=N` | Peppered | exact datetime, hall, 3-level status | **live** — 619 |
| Tilburg | `/nl/agenda?page=N` | Ticketmatic | sold-out flag | **live** — 514 |
| Parktheater | `/programma?page=N` | Peppered | exact datetime, hall, 3-level status | **live** — 319 |
| Verkadefabriek | `/podium?page=N` | — | empty vs. badge (weak) | **live** — 90 |
| MgE | client-rendered (SEO stubs only) | Tixly | needs its XHR endpoint found | todo |
| Paradox / 013 | server-rendered, custom markup | — | not yet parsed | todo |

**Peppered is the good case.** Chassé and Parktheater share markup exactly, so
one `PepperedAdapter` serves both and the venue classes are just metadata. Every
performance anchor carries `data-event-start`, `data-event-hall` and a `status-*`
class, so nothing is guessed and availability has three levels, not two.

**Verkadefabriek is the weak case.** A show on sale has an *empty*
`.ticket-status` div; only a sold-out one has a badge. Absence-as-signal breaks
silently, so the adapter warns if it finds events but no `.ticket-status`
elements at all.

**MgE is the blocked case.** Its agenda renders client-side — the 264 event
links in the HTML are hidden SEO placeholders with no dates or availability.
`wp-json` is locked (401) and `tickets.mge.nl/nl/events/` redirects to Tixly's
corporate site. Next step is a browser session to capture what the agenda
actually fetches.

**Exact seat counts are reachable.** Ticketmatic's widget renders
`"{n} stoelen beschikbaar"` and exposes
`/widgets/backend/{account}/{lang}/eventseatingplan/{code}/availability/...`.
The blocker is that `{code}` is a hashed event reference, not the numeric event
id we already store in `shows.ticket_ref`. Cracking that upgrades Tilburg (and
any other Ticketmatic venue) from a binary flag to a real seat count.

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
run_once.py
deploy/install.sh    systemd timer install
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

Live on the Pi as `podium-poller.timer`, four polls a day (06/12/18/22, with a
15-minute random delay), `Persistent=true` so a missed run still collects.

```bash
ssh sausje@raspberrypi "cd ~/podium-watcher && git pull --ff-only && sudo systemctl start podium-poller.service"
systemctl list-timers podium-poller.timer --no-pager
journalctl -u podium-poller -n 50 --no-pager
```

`.gitattributes` forces LF: the repo is edited on Windows and run on Linux, and
a CRLF `install.sh` fails with "bad interpreter".

## Still to build

- MgE adapter (find its XHR endpoint), then Paradox and 013
- Ticketmatic seat counts — the widget renders `"{n} stoelen beschikbaar"` and
  exposes `eventseatingplan/{code}/availability/`, but `{code}` is a hashed
  reference, not the numeric id in `shows.ticket_ref`
- Hall capacities, so `seats_free` becomes a fraction rather than a bare count
- Forecast model over the fill curves
- Dashboard: next 7–14 days ranked, plus a calendar month view
- ntfy push when a window opens; weekly email digest
- One-tap *got it / missed it* logging, to learn whether venues cap the youth
  allocation separately from the hall
