"""Shared adapter for venues running on the Peppered ticketing platform.

Chasse (Breda) and Parktheater (Eindhoven) both use it, and their markup is
identical enough that one parser handles both unchanged -- only the base URL
and the venue metadata differ.

A production holds one or more performances; we store one row per
*performance*, because that is the thing that sells out and the thing you
can attend.

Peppered renders a performance in one of two shapes, and getting only the
first one wrong cost us every sold-out show in the database:

1. **On sale** -- an order anchor carrying `data-event-id`,
   `data-event-start`, `data-event-hall` and a `status-*` class.
2. **Not on sale** (sold out, or info-only) -- a bare link to the production
   page with `class="status-info status-uitverkocht"` and *no data
   attributes at all*. Date, time and hall live on the card instead.

Selecting only `a[data-event-id]` therefore skipped every sold-out
performance silently: it did not record them as sold out, it dropped them
entirely, so a show that sold out simply vanished from the data and its fill
curve ended without capturing the one event that matters most.

Because of that, identity cannot be `data-event-id` -- it disappears the
moment a show sells out, and the same performance would come back as a new
row. The key is production id + start time, which is observable in both
shapes.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Show, Snapshot
from .base import VenueAdapter, clean, euros_to_cents, parse_dutch_datetime

log = logging.getLogger(__name__)

_HAS_DIGITS = re.compile(r"\d")


def cls_norm(text: str) -> str:
    r"""Collapse whitespace, NBSP included.

    Peppered's price block is full of &nbsp;. Python's \s matches those, so
    one substitution is enough.
    """
    return re.sub(r"\s+", " ", text).strip()


# Peppered's per-performance sale states.
STATUS_MAP = {
    "normaal": AVAILABLE,
    "laatste_kaarten": LIMITED,
    "bijna_uitverkocht": LIMITED,
    "uitverkocht": SOLD_OUT,
    "wachtlijst": SOLD_OUT,  # a waiting list means the seats are gone
    "geannuleerd": UNKNOWN,
    "info": UNKNOWN,  # no direct sale, info page only
    # First seen at De Doelen, where a third of the programme is sold by the
    # Rotterdams Philharmonisch or Ticketmaster rather than the hall itself.
    "externe_verkoop": AVAILABLE,
    "vrij_entree": AVAILABLE,
}


class PepperedAdapter(VenueAdapter):
    base_url: str = ""
    programme_path: str = "/programma"
    max_pages: int = 40

    # Below this, a figure in the price range is a placeholder rather than a
    # seat anyone buys.
    MIN_PLAUSIBLE_CENTS = 500

    @property
    def programme_url(self) -> str:
        return self.base_url + self.programme_path

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        # Pages are walked by their own "next" link. Peppered renamed its page
        # parameter from `page` to `p54_page` in late September 2026, and the
        # hand-built `?page=N` then returned page one forever.
        results = self.paginate(self.programme_url, self._parse_page, self.max_pages)
        log.info(
            "%s: %s performances across %s pages",
            self.venue_id, len(results), self.pages_read,
        )
        return results

    # ---- parsing -------------------------------------------------------

    def _parse_page(self, html: str) -> list[tuple[Show, Snapshot]]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[tuple[Show, Snapshot]] = []
        for card in soup.select("li.eventCard"):
            out.extend(self._parse_card(card))
        return out

    def _parse_card(self, card) -> list[tuple[Show, Snapshot]]:
        production_id = card.get("data-entry-id")
        title_el = card.select_one(".title")
        title = clean(title_el.get_text()) if title_el else None
        if not production_id or not title:
            return []

        subtitle_el = card.select_one(".subtitle")
        subtitle = clean(subtitle_el.get_text()) if subtitle_el else None

        # The production page is what a person wants to open -- the order
        # anchor goes straight into a basket.
        desc = card.select_one("a.desc") or card.select_one("a.image")
        href = desc.get("href") if desc else None
        url = (
            (self.base_url + href) if href and href.startswith("/") else
            (href or self.programme_url)
        )

        price_min, price_max, price_student = self._prices(self._price_text(card))

        prod_type = None
        for cls in card.get("class") or []:
            if cls.startswith("production-type-"):
                prod_type = cls[len("production-type-"):]
                break

        common = dict(
            production_id=production_id,
            title=title,
            subtitle=subtitle,
            url=url,
            prod_type=prod_type or "default",
            price_min=price_min,
            price_max=price_max,
            price_student=price_student,
        )

        order_anchors = card.select("a[data-event-id]")
        if order_anchors:
            out = []
            for a in order_anchors:
                parsed = self._from_order_anchor(a, **common)
                if parsed:
                    out.append(parsed)
            return out

        # No order anchor: the performance is not buyable. Read it off the
        # card, which is where the date, time and hall live in this shape.
        parsed = self._from_card_only(card, **common)
        return [parsed] if parsed else []

    def _from_order_anchor(
        self, a, *, production_id, title, subtitle, url, prod_type,
        price_min, price_max, price_student,
    ) -> tuple[Show, Snapshot] | None:
        starts_at = None
        raw_start = a.get("data-event-start")
        if raw_start:
            try:
                from datetime import datetime

                starts_at = datetime.strptime(raw_start, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                log.debug("%s: unparsable start %r", self.venue_id, raw_start)
        if starts_at is None:
            return None

        return (
            Show(
                venue_id=self.venue_id,
                external_id=self._key(production_id, starts_at),
                title=title,
                subtitle=subtitle,
                starts_at=starts_at,
                url=url,
                hall=clean(a.get("data-event-hall")),
                tags=[prod_type],
                ticket_url=a.get("href"),
                ticket_ref=a.get("data-event-id"),
            ),
            Snapshot(
                state=self._state_from_classes(a.get("class") or []),
                price_min_cents=price_min,
                price_max_cents=price_max,
                price_student_cents=price_student,
            ),
        )

    def _from_card_only(
        self, card, *, production_id, title, subtitle, url, prod_type,
        price_min, price_max, price_student,
    ) -> tuple[Show, Snapshot] | None:
        top = card.select_one(".top-date")
        if top is None:
            return None
        # A start/end pair means a run of several nights summarised on one
        # card. Without per-date rows we cannot say which night this is, and
        # inventing one would be worse than recording nothing.
        if top.select_one(".end"):
            log.debug(
                "%s: skipping multi-date card %s with no per-date rows",
                self.venue_id, production_id,
            )
            return None

        start_el = top.select_one(".start")
        time_el = top.select_one(".time")
        date_text = clean(start_el.get_text()) if start_el else None
        time_text = clean(time_el.get_text()) if time_el else None

        # "Meerdere tijdstippen" is the same problem in a different shape:
        # several performances that day, summarised, with no way to tell them
        # apart. Recorded as one row it landed at midnight -- wrong time and
        # wrong count. 26 Parktheater rows looked like real data because of it.
        if not time_text or not _HAS_DIGITS.search(time_text):
            log.debug(
                "%s: skipping card %s with unresolvable time %r",
                self.venue_id, production_id, time_text,
            )
            return None

        starts_at = parse_dutch_datetime(date_text or "", time_text)
        if starts_at is None:
            return None

        venue_el = card.select_one(".venue")
        status_anchor = card.select_one("a[class*=status-]")
        classes = status_anchor.get("class") if status_anchor else []

        return (
            Show(
                venue_id=self.venue_id,
                external_id=self._key(production_id, starts_at),
                title=title,
                subtitle=subtitle,
                starts_at=starts_at,
                url=url,
                hall=clean(venue_el.get_text()) if venue_el else None,
                tags=[prod_type],
                ticket_url=None,  # nothing to buy
                ticket_ref=None,
            ),
            Snapshot(
                state=self._state_from_classes(classes or []),
                price_min_cents=price_min,
                price_max_cents=price_max,
                price_student_cents=price_student,
            ),
        )

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _key(production_id: str, starts_at) -> str:
        """Stable across sale states.

        `data-event-id` vanishes when a performance sells out, so keying on
        it would file the same night as a new show the moment it matters.
        """
        return f"{production_id}-{starts_at:%Y%m%d%H%M}"

    @staticmethod
    def _state_from_classes(classes: list[str]) -> str:
        """`status-info status-uitverkocht` must read as sold out, not info.

        The classes arrive in the generic-to-specific order Peppered emits,
        so the most specific recognised one wins rather than the first.
        """
        best = UNKNOWN
        for cls in classes:
            if not cls.startswith("status-"):
                continue
            token = cls[len("status-"):]
            if not token:
                continue
            mapped = STATUS_MAP.get(token)
            if mapped is None:
                continue
            if mapped != UNKNOWN or best == UNKNOWN:
                best = mapped
            if mapped in (SOLD_OUT, LIMITED, AVAILABLE):
                best = mapped
        return best

    # Subscription tiers that are not open to a walk-up buyer. Treating a
    # Cineville fare as the base price would understate every film.
    IGNORED_PRICE_LABELS = (
        "cineville", "kidspas", "bredapas", "abonnement", "kinderen",
        "begeleider", "vriend", "donateur",
        # Rotterdam city passes, and Theater Rotterdam's own sale tier. Both
        # are real fares, neither is one you can walk up and buy.
        "rotterdampas", "sirenesale",
    )
    # Theater Rotterdam labels its youth rate by age rather than by status --
    # "t/m 30 jaar € 10,00" beside "Standaard € 22,00". Read as an unlabelled
    # category it became the base price, so the show reported a €10 door price
    # and a €0 saving: the best deal on the board, rendered invisible.
    STUDENT_PRICE_LABELS = ("student", "scholier", "cjp", "jongeren")
    YOUTH_AGE_LABEL = re.compile(r"t/m\s*\d{2}\s*jaar|onder\s*(?:de\s*)?\d{2}|<\s*\d{2}")

    # Labels that mark the ordinary walk-up fare. Theater Rotterdam says
    # "Standaard" where the others say "normaal".
    BASE_PRICE_LABELS = ("normaal", "standaard")

    @staticmethod
    def _price_text(card) -> str | None:
        """The labelled fare table beats the bare range printed beside it.

        Theater Rotterdam renders both: a popover table naming every fare
        ("Standaard € 22,00 / Rotterdampas € 17,00 / t/m 30 jaar € 10,00")
        and a summary div showing only its span ("€ 10,00–€ 22,00"). The
        summary sits first in the markup, so `.price` selected the shape with
        no labels -- and the €10 youth rate was read as the door price, which
        made the best deal on the board look like no deal at all.

        Chasse, Parktheater and de Doelen print no such table, so they keep
        falling through to `.price` exactly as before.
        """
        tables = [t for t in card.find_all("table") if t.select_one("td.price")]
        if tables:
            return cls_norm(" ".join(t.get_text(" ", strip=True) for t in tables))
        el = card.select_one(".price")
        return cls_norm(el.get_text(" ", strip=True)) if el else None

    @classmethod
    def _price_categories(cls, text: str) -> list[tuple[int, str]]:
        """'volloopplaats normaal € 14,50 student € 10,00' -> [(1450, ...)].

        Labels precede their amount, so each '€' chunk begins with the amount
        belonging to the label accumulated so far and ends with the label for
        the next one.
        """
        chunks = text.split("€")
        if len(chunks) < 2:
            return []
        out: list[tuple[int, str]] = []
        label = chunks[0].strip()
        for chunk in chunks[1:]:
            m = re.match(r"\s*([\d.,]+)", chunk)
            if not m:
                label = chunk.strip()
                continue
            cents = euros_to_cents(m.group(1).rstrip(".,"))
            out.append((cents or 0, label.lower()))
            label = chunk[m.end():].strip()
        return out

    @classmethod
    def _prices(cls, text: str | None) -> tuple[int | None, int | None, int | None]:
        """-> (normal_min, normal_max, published_student_price).

        Peppered writes the price block two ways. Sometimes it is a bare range
        ('€ 0,00–€ 13,00'), where the floor is a placeholder nobody pays.
        Sometimes it is labelled categories, and then the labels matter a great
        deal: 'volloopplaats normaal € 14,50 scholier/student/CJP € 10,00'
        means the €10 *is already the student fare*. Taking the minimum across
        all categories and then applying Chasse's 50% discount to it billed a
        €14,50 seat as €5 -- wrong twice over, because it halved a price that
        was already discounted.

        So: 'normaal' rows set the base price, a student/CJP row is used
        directly as the fare, and subscription tiers are ignored entirely.
        """
        if not text:
            return None, None, None

        cats = cls._price_categories(text)
        if not cats:
            return None, None, None

        student = None
        normal: list[int] = []
        unlabelled: list[int] = []
        for cents, label in cats:
            if not cents or cents < cls.MIN_PLAUSIBLE_CENTS:
                continue
            if any(g in label for g in cls.IGNORED_PRICE_LABELS):
                continue
            if any(g in label for g in cls.STUDENT_PRICE_LABELS) or \
                    cls.YOUTH_AGE_LABEL.search(label):
                student = cents if student is None else min(student, cents)
            elif any(g in label for g in cls.BASE_PRICE_LABELS):
                normal.append(cents)
            else:
                unlabelled.append(cents)

        base = normal or unlabelled
        if not base:
            return None, None, student
        return min(base), max(base), student
