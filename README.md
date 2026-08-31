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
| Tilburg | server-rendered, `?page=N` | Ticketmatic | sold-out flag | **live** |
| MgE | client-rendered (SEO stubs only) | Tixly | detail pages needed | todo |
| Verkadefabriek | server-rendered, no pagination | — | sold-out flag | todo |
| Chassé | server-rendered | — | sold-out + waitlist | todo |
| Parktheater | not yet probed | — | — | todo |
| Paradox / 013 | not yet probed | — | — | todo |

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
    tilburg.py       Schouwburg & Concertzaal Tilburg
run_once.py
```

One broken venue never stops the others — failures are recorded in `poll_runs`
so a silently-broken adapter looks different from a venue with nothing on.

## Still to build

- Remaining six venue adapters
- Ticketmatic seat counts (hashed event code)
- Forecast model over the fill curves
- Dashboard: next 7–14 days ranked, plus a calendar month view
- ntfy push when a window opens; weekly email digest
- One-tap *got it / missed it* logging, to learn whether venues cap the youth
  allocation separately from the hall
