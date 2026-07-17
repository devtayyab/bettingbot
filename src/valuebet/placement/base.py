"""Placement interface and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PlacementRequest:
    event_id: str
    market_type: str
    selection: str
    target_odds: float
    stake: float
    # Reject if the live price has dropped below this (odds moved against us).
    min_odds: float


@dataclass
class PlacementResult:
    success: bool
    placed_odds: float | None
    # Feature 3: Track both what we asked for and what was actually accepted.
    # A bookmaker may silently reduce the stake (account limitation signal).
    requested_stake: float          # What we asked to stake
    accepted_stake: float           # What the bookmaker actually accepted
    dry_run: bool
    message: str
    # True when the bookmaker accepted a materially smaller stake than requested.
    # Used by limit_tracker to flag potential account limitation.
    was_stake_reduced: bool = False

    def __post_init__(self) -> None:
        # Auto-detect stake reduction: more than 5% below requested
        if (
            not self.dry_run
            and self.requested_stake > 0
            and self.accepted_stake < self.requested_stake * 0.95
        ):
            object.__setattr__(self, "was_stake_reduced", True)

    # Backwards-compatible alias so existing callers using .stake still work.
    @property
    def stake(self) -> float:
        return self.accepted_stake


class BetPlacer(Protocol):
    def place(self, request: PlacementRequest) -> PlacementResult:
        ...
