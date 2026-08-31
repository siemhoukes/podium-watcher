"""Venue adapter registry."""

from .chasse import ChasseAdapter
from .mge import MgeAdapter
from .paradox import ParadoxAdapter
from .parktheater import ParktheaterAdapter
from .poppodium013 import Poppodium013Adapter
from .tilburg import TilburgAdapter
from .verkadefabriek import VerkadefabriekAdapter

ADAPTERS = [
    TilburgAdapter,
    ChasseAdapter,
    ParktheaterAdapter,
    VerkadefabriekAdapter,
    MgeAdapter,
    ParadoxAdapter,
    Poppodium013Adapter,
]

__all__ = [
    "ADAPTERS",
    "TilburgAdapter",
    "ChasseAdapter",
    "ParktheaterAdapter",
    "VerkadefabriekAdapter",
    "MgeAdapter",
    "ParadoxAdapter",
    "Poppodium013Adapter",
]
