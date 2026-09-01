"""Venue adapter registry.

TivoliVredenburg is imported but **not** in ADAPTERS. Its site sits behind
Cloudflare bot protection that answers the Pi with `cf-mitigated: challenge`
and a 403 on every request, whatever user agent is used -- while de Doelen
answers the same machine normally, so it is not the network. That is the venue
declining automated access, and getting round it would mean defeating a
control they deliberately switched on.

The adapter and its tests stay, so it is one line to re-enable if that ever
changes.
"""

from .chasse import ChasseAdapter
from .doelen import DoelenAdapter
from .mge import MgeAdapter
from .paradox import ParadoxAdapter
from .parktheater import ParktheaterAdapter
from .poppodium013 import Poppodium013Adapter
from .tilburg import TilburgAdapter
from .tivoli import TivoliAdapter
from .verkadefabriek import VerkadefabriekAdapter

ADAPTERS = [
    TilburgAdapter,
    ChasseAdapter,
    ParktheaterAdapter,
    VerkadefabriekAdapter,
    MgeAdapter,
    DoelenAdapter,
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
    "DoelenAdapter",
    "TivoliAdapter",
    "ParadoxAdapter",
    "Poppodium013Adapter",
]
