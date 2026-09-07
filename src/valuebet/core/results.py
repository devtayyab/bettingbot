"""Result resolving module to fetch actual event outcomes and settle bets."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from ..logging import get_logger

log = get_logger("core.results")


class BetOutcome(str, Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"
    HALF_WON = "half_won"
    HALF_LOST = "half_lost"


@dataclass
class EventResult:
    event_id: str
    sport: str
    home_score: int
    away_score: int
    completed: bool
    completed_at: datetime | None


class ResultResolver(Protocol):
    """Protocol for fetching real-world match results to settle bets."""
    
    def fetch_result(self, event_id: str, sport: str) -> EventResult | None:
        ...

    def determine_outcome(self, result: EventResult, market_type: str, selection: str) -> BetOutcome:
        """Evaluates whether the selection won or lost based on the final score."""
        ...


class MockResultResolver:
    """TEST ONLY — invents match results with `random`.

    Never wire this into settlement: it would write fabricated wins, losses and
    profit into the bets table and report them as realised P&L. Replace with a real
    results feed (The-Odds-API scores, Sportmonks, API-Football) before enabling
    automatic settlement.
    """

    def fetch_result(self, event_id: str, sport: str) -> EventResult | None:
        # In a real system, this would call The-Odds-API, Sportmonks, or API-Football.
        # We simulate a completed match randomly.
        completed = random.choice([True, False])
        if not completed:
            return None
            
        home_score = random.randint(0, 3)
        away_score = random.randint(0, 3)
        
        return EventResult(
            event_id=event_id,
            sport=sport,
            home_score=home_score,
            away_score=away_score,
            completed=True,
            completed_at=datetime.now(UTC)
        )

    def determine_outcome(self, result: EventResult, market_type: str, selection: str) -> BetOutcome:
        if market_type == "MATCH_ODDS":
            if result.home_score > result.away_score:
                winning_selection = "Home"
            elif result.away_score > result.home_score:
                winning_selection = "Away"
            else:
                winning_selection = "Draw"
                
            # Naive matching: if the selection string contains the winning team type
            if winning_selection.lower() in selection.lower():
                return BetOutcome.WON
            return BetOutcome.LOST
            
        # For unknown markets in the mock, randomly resolve
        return random.choice([BetOutcome.WON, BetOutcome.LOST])

    def resolve(self, bet) -> BetOutcome:
        """Resolve one bet. Returns PENDING when no result is available.

        Callers previously invoked this method, which did not exist, and compared
        against BetOutcome.UNKNOWN/WIN/LOSS, which are not members of the enum.
        """
        result = self.fetch_result(str(getattr(bet, "signal_id", "")), "unknown")
        if result is None:
            return BetOutcome.PENDING
        return self.determine_outcome(result, "MATCH_ODDS", bet.selection)
