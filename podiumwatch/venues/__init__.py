"""Venue adapter registry."""

from .tilburg import TilburgAdapter

ADAPTERS = [
    TilburgAdapter,
]

__all__ = ["ADAPTERS", "TilburgAdapter"]
