"""Venue adapter registry."""

from .chasse import ChasseAdapter
from .mge import MgeAdapter
from .parktheater import ParktheaterAdapter
from .tilburg import TilburgAdapter
from .verkadefabriek import VerkadefabriekAdapter

ADAPTERS = [
    TilburgAdapter,
    ChasseAdapter,
    ParktheaterAdapter,
    VerkadefabriekAdapter,
    MgeAdapter,
]

__all__ = [
    "ADAPTERS",
    "TilburgAdapter",
    "ChasseAdapter",
    "ParktheaterAdapter",
    "VerkadefabriekAdapter",
    "MgeAdapter",
]
