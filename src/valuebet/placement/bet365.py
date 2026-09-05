"""Bet365 placer — NOT IMPLEMENTED.

There is no Bet365 automation yet. This placer exists so the router has an entry
for the book, and it deliberately reports failure: the previous version returned
`success=True` with the message "Dummy Bet365 placement successful", which made the
dashboard record a bet that had never been sent anywhere.

Implement `place()` against the real site before routing any signal here.
"""

from __future__ import annotations

from ..logging import get_logger
from .base import BetPlacer, PlacementRequest, PlacementResult, PlacementStatus

log = get_logger("placement.bet365")

BOOKMAKER_NAME = "bet365"


class Bet365Placer(BetPlacer):
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def place(self, req: PlacementRequest) -> PlacementResult:
        # `event` is structlog's reserved key for the message itself — passing it as
        # a kwarg raises TypeError, which used to take down the whole execute cycle.
        log.error(
            "bet365_not_implemented",
            event_ref=req.event_id,
            selection=req.selection,
            stake=req.stake,
            odds=req.target_odds,
        )
        return PlacementResult(
            success=False,
            placed_odds=None,
            requested_stake=req.stake,
            accepted_stake=0.0,
            dry_run=False,
            message="bet365 placement is not implemented; no bet was sent",
            bookmaker=BOOKMAKER_NAME,
            status=PlacementStatus.ERROR,
            verified_on_platform=False,
        )

    def close(self) -> None:
        return None
