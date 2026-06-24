"""Common interface every odds source implements.

The engine depends only on this protocol, so Betfair, Pinnacle, a mock replay
source, or any future book are interchangeable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..core.models import MarketSnapshot, Sport


@runtime_checkable
class OddsSource(Protocol):
    name: str

    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        """Return current market snapshots for the given sport.

        Implementations must be side-effect free w.r.t. our DB and should raise
        on auth/transport errors so the scheduler can back off and retry.
        """
        ...
