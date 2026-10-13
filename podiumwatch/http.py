"""Polite HTTP session shared by every venue adapter.

These are small regional theatres, not APIs built for us. One request per
agenda page per run, a real User-Agent, generous timeouts and a little jitter
so seven venues never get hit in lockstep.
"""

from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "(+podium-watcher; personal last-minute ticket tracker)"
)

DEFAULT_TIMEOUT = 30
MAX_ATTEMPTS = 3

# Pages that answer 200 (or 403) but are not the page we asked for. Parktheater
# switches on a virtual waiting room during busy sales, and Chasse and Theater
# Rotterdam sit behind a BunnyCDN JavaScript check. Parsed as an agenda, each
# of these is simply "a page with no shows on it" -- a clean run, finding
# nothing. Recognising them turns that into a failure with a reason.
#
# This only *detects* them. The poller does not try to get past either: a
# waiting room or a bot check is the venue saying "not now", and the next run
# simply tries again.
BLOCK_MARKERS = (
    ("/csq/queue", "virtuele wachtrij"),
    ("Je staat in de wachtrij", "virtuele wachtrij"),
    ("Establishing a secure connection", "botcheck"),
    ("Please enable JavaScript to continue", "botcheck"),
)
# Interstitials are small; a real agenda page is hundreds of kilobytes.
BLOCK_PAGE_MAX_BYTES = 50_000


def block_reason(text: str | None) -> str | None:
    """Why this page is a waiting room or bot check, or None if it is real."""
    if not text or len(text) > BLOCK_PAGE_MAX_BYTES:
        return None
    for marker, reason in BLOCK_MARKERS:
        if marker in text:
            return reason
    return None


class Fetcher:
    def __init__(self, min_delay: float = 1.0, max_delay: float = 3.0):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            }
        )
        self.min_delay = min_delay
        self.max_delay = max_delay
        # Why the last `get` returned None, in words fit for /status.
        self.last_problem: str | None = None

    def _pause(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def get(self, url: str, **kwargs) -> requests.Response | None:
        """GET with retries. Returns None if the URL is unreachable."""
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        self.last_problem = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.session.get(url, **kwargs)
                blocked = None
                if (not kwargs.get("stream")
                        and len(resp.content) <= BLOCK_PAGE_MAX_BYTES):
                    blocked = block_reason(resp.text)
                if resp.status_code == 200 and not blocked:
                    self._pause()
                    return resp
                if blocked:
                    self.last_problem = f"{blocked} (HTTP {resp.status_code})"
                    log.warning("GET %s -> %s, not retrying", url, self.last_problem)
                    self._pause()
                    return None
                # 4xx other than 429 won't improve on retry.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    self.last_problem = f"HTTP {resp.status_code}"
                    log.warning("GET %s -> HTTP %s, not retrying", url, resp.status_code)
                    return None
                self.last_problem = f"HTTP {resp.status_code}"
                log.warning(
                    "GET %s -> HTTP %s (attempt %s/%s)",
                    url,
                    resp.status_code,
                    attempt,
                    MAX_ATTEMPTS,
                )
            except requests.RequestException as exc:
                self.last_problem = f"netwerkfout: {type(exc).__name__}"
                log.warning(
                    "GET %s failed: %s (attempt %s/%s)", url, exc, attempt, MAX_ATTEMPTS
                )
            if attempt < MAX_ATTEMPTS:
                time.sleep(2**attempt + random.uniform(0, 1))
        return None

    def get_text(self, url: str, max_bytes: int | None = None, **kwargs) -> str | None:
        """Fetch text, optionally stopping after `max_bytes`.

        Verkadefabriek's Ticketmatic widget is 5.7 MB, of which the event JSON
        we want ends by about 60 KB; the rest is bundle. Downloading all of it
        forty times a run is pure waste, so callers that only need the head of
        a document can cap it.
        """
        if max_bytes:
            kwargs["stream"] = True
        resp = self.get(url, **kwargs)
        if resp is None:
            return None
        # These sites are UTF-8 but do not always say so in the headers,
        # which makes requests fall back to latin-1 and mangle every accent.
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"

        if not max_bytes:
            return resp.text

        try:
            chunks, total = [], 0
            for chunk in resp.iter_content(chunk_size=65536):
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            return b"".join(chunks).decode(resp.encoding or "utf-8", "replace")
        finally:
            resp.close()
