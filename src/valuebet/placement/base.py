"""Placement interface and result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class PlacementStatus(str, Enum):
    """What actually happened at the bookmaker.

    The distinction matters: `success` alone is ambiguous — a dry run "succeeds"
    without any money being staked, and an UNCONFIRMED placement may or may not
    have been struck. Callers must branch on this, not on `success`.
    """

    PLACED = "placed"            # Receipt read back from the book: real money is on.
    DRY_RUN = "dry_run"          # Slip prepared, place button deliberately NOT clicked.
    UNCONFIRMED = "unconfirmed"  # Place was clicked but no receipt — needs manual check!
    REJECTED = "rejected"        # Book refused, or we abandoned on price/limits.
    ERROR = "error"              # Automation/session failure before committing.


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
    # Which book this result came from, so bets are attributed correctly.
    bookmaker: str = "stoiximan"
    # Authoritative outcome. Defaults are derived in __post_init__ for callers
    # that still construct results with only success/dry_run.
    status: PlacementStatus | None = None
    # Set when we re-read the book's own open-bets list and found the bet there.
    # This is the only evidence that the bet exists on the platform.
    verified_on_platform: bool = False

    def __post_init__(self) -> None:
        # Auto-detect stake reduction: more than 5% below requested
        if (
            not self.dry_run
            and self.requested_stake > 0
            and self.accepted_stake < self.requested_stake * 0.95
        ):
            object.__setattr__(self, "was_stake_reduced", True)

        if self.status is None:
            if self.dry_run:
                derived = PlacementStatus.DRY_RUN
            elif self.success:
                derived = PlacementStatus.PLACED
            else:
                derived = PlacementStatus.REJECTED
            object.__setattr__(self, "status", derived)

    @property
    def is_real_bet(self) -> bool:
        """True only when real money was actually committed at the bookmaker.

        A dry run is never a real bet. An UNCONFIRMED result *might* be one, so
        it counts here: the stake may be at risk and must not be ignored.
        """
        return not self.dry_run and self.status in (
            PlacementStatus.PLACED,
            PlacementStatus.UNCONFIRMED,
        )

    @property
    def needs_manual_check(self) -> bool:
        """True when we cannot tell whether the bet landed. Operator must look."""
        return self.status == PlacementStatus.UNCONFIRMED

    # Backwards-compatible alias so existing callers using .stake still work.
    @property
    def stake(self) -> float:
        return self.accepted_stake


class BetPlacer(Protocol):
    def place(self, request: PlacementRequest) -> PlacementResult:
        ...

    def close(self) -> None:
        """Release any browser/session resources held by this placer."""
        ...
