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
    stake: float
    dry_run: bool
    message: str


class BetPlacer(Protocol):
    def place(self, request: PlacementRequest) -> PlacementResult:
        ...
