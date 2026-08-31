"""Fixtures are real pages, saved and gzipped.

Scrapers fail silently: a venue redesigns, the selector stops matching, and the
poller cheerfully records "nothing on" for weeks. These tests parse captured
copies of the real markup and assert on values taken from them, so a change in
shape breaks a test instead of quietly rotting the data.

Refresh a fixture with `python -m tests.refresh <name>` when a venue changes
on purpose -- and expect to update the asserted numbers with it.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_text(name: str) -> str:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load_json(name: str):
    return json.loads(load_text(name))


class FakeFetcher:
    """Serves fixtures instead of the internet.

    Records every URL asked for, so tests can also assert that an adapter did
    not wander off and make requests it should not.
    """

    def __init__(self, pages: dict[str, str] | None = None, default: str | None = None):
        self.pages = pages or {}
        self.default = default
        self.requested: list[str] = []

    def _match(self, url: str) -> str | None:
        for needle, body in self.pages.items():
            if needle in url:
                return body
        return self.default

    def get_text(self, url: str, max_bytes: int | None = None, **kwargs) -> str | None:
        self.requested.append(url)
        return self._match(url)

    def get(self, url: str, **kwargs):
        self.requested.append(url)
        body = self._match(url)
        if body is None:
            return None
        return _FakeResponse(body)


class _FakeResponse:
    def __init__(self, body: str):
        self.text = body
        self.status_code = 200

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def fake_fetcher():
    return FakeFetcher
