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

    def _pause(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def get(self, url: str, **kwargs) -> requests.Response | None:
        """GET with retries. Returns None if the URL is unreachable."""
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.session.get(url, **kwargs)
                if resp.status_code == 200:
                    self._pause()
                    return resp
                # 4xx other than 429 won't improve on retry.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    log.warning("GET %s -> HTTP %s, not retrying", url, resp.status_code)
                    return None
                log.warning(
                    "GET %s -> HTTP %s (attempt %s/%s)",
                    url,
                    resp.status_code,
                    attempt,
                    MAX_ATTEMPTS,
                )
            except requests.RequestException as exc:
                log.warning(
                    "GET %s failed: %s (attempt %s/%s)", url, exc, attempt, MAX_ATTEMPTS
                )
            if attempt < MAX_ATTEMPTS:
                time.sleep(2**attempt + random.uniform(0, 1))
        return None

    def get_text(self, url: str, **kwargs) -> str | None:
        resp = self.get(url, **kwargs)
        if resp is None:
            return None
        # These sites are UTF-8 but do not always say so in the headers,
        # which makes requests fall back to latin-1 and mangle every accent.
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
