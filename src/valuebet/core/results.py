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


class TheOddsAPIResultResolver:
    """Official result resolver using The Odds API scores endpoint (/v4/sports/{sport}/scores/)."""

    def __init__(self, api_key: str | None = None) -> None:
        from ..config import get_settings
        self.api_key = api_key or get_settings().the_odds_api_key
        self.base_url = "https://api.the-odds-api.com/v4"
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._cache_ttl = 300.0  # 5 minutes

    def fetch_scores_for_sport(self, sport_name: str, days_from: int = 3) -> list[dict]:
        """Fetch completed events for all configured leagues in the given sport."""
        if not self.api_key:
            return []
        import time
        import httpx
        from ..core.models import Sport
        from ..sources.the_odds_api import _SPORT_KEYS

        try:
            sport_enum = Sport(sport_name)
        except ValueError:
            return []

        sport_keys = _SPORT_KEYS.get(sport_enum, [])
        all_events = []
        for key in sport_keys:
            cache_hit = self._cache.get(key)
            if cache_hit and (time.time() - cache_hit[0]) < self._cache_ttl:
                all_events.extend(cache_hit[1])
                continue
            try:
                resp = httpx.get(
                    f"{self.base_url}/sports/{key}/scores/",
                    params={"apiKey": self.api_key, "daysFrom": days_from},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    events = resp.json()
                    self._cache[key] = (time.time(), events)
                    all_events.extend(events)
                elif resp.status_code in (401, 429):
                    log.warning("odds_api_scores_rate_limited", status=resp.status_code)
                    break
            except Exception as exc:
                log.warning("odds_api_fetch_scores_error", sport_key=key, error=str(exc))
        return all_events

    def determine_match_outcome(
        self,
        selection: str,
        home_team: str,
        away_team: str,
        scores: list[dict],
    ) -> BetOutcome:
        """Evaluate outcome based on official final scores."""
        if not scores:
            return BetOutcome.PENDING

        home_score: float | None = None
        away_score: float | None = None
        for s in scores:
            name = s.get("name", "")
            try:
                val = float(s.get("score", 0))
                if name.lower() == home_team.lower():
                    home_score = val
                elif name.lower() == away_team.lower():
                    away_score = val
            except (ValueError, TypeError):
                pass

        if home_score is None or away_score is None:
            return BetOutcome.PENDING

        sel_lower = selection.strip().lower()
        is_draw = sel_lower in ("draw", "tie", "x")

        if home_score > away_score:
            if not is_draw and (sel_lower in home_team.lower() or home_team.lower() in sel_lower):
                return BetOutcome.WON
            return BetOutcome.LOST
        elif away_score > home_score:
            if not is_draw and (sel_lower in away_team.lower() or away_team.lower() in sel_lower):
                return BetOutcome.WON
            return BetOutcome.LOST
        else:
            # Tied
            if is_draw:
                return BetOutcome.WON
            return BetOutcome.LOST
