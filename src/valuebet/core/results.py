"""Result resolving module to fetch actual event outcomes and settle bets."""

from __future__ import annotations

import random
import re
import time
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
    profit into the bets table and report them as realised P&L.
    """

    def fetch_result(self, event_id: str, sport: str) -> EventResult | None:
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
            completed_at=datetime.now(UTC),
        )

    def determine_outcome(self, result: EventResult, market_type: str, selection: str) -> BetOutcome:
        if market_type == "MATCH_ODDS":
            if result.home_score > result.away_score:
                winning_selection = "Home"
            elif result.away_score > result.home_score:
                winning_selection = "Away"
            else:
                winning_selection = "Draw"

            if winning_selection.lower() in selection.lower():
                return BetOutcome.WON
            return BetOutcome.LOST

        return random.choice([BetOutcome.WON, BetOutcome.LOST])

    def resolve(self, bet) -> BetOutcome:
        """Resolve one bet. Returns PENDING when no result is available."""
        result = self.fetch_result(str(getattr(bet, "signal_id", "")), "unknown")
        if result is None:
            return BetOutcome.PENDING
        return self.determine_outcome(result, "MATCH_ODDS", bet.selection)


# Module-level cache so periodic scheduler runs reuse fetched league scores
# instead of burning API quota every 15 minutes across every league.
_SCORES_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SCORES_CACHE_TTL = 600.0  # 10 minutes
_SCORES_QUOTA_EXHAUSTED_UNTIL: tuple[str, float] | None = None

_OVER_UNDER_RE = re.compile(r"\b(over|under)\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_HANDICAP_SEL_RE = re.compile(r"^(.*?)\s*(?:\(\s*([+-]?\d+(?:\.\d+)?)\s*\)|([+-]\d+(?:\.\d+)?))\s*$")


class TheOddsAPIResultResolver:
    """Official result resolver using The Odds API scores endpoint (/v4/sports/{sport}/scores/)."""

    def __init__(self, api_key: str | None = None) -> None:
        from ..config import get_settings

        self.api_key = api_key if api_key is not None else get_settings().the_odds_api_key
        self.base_url = "https://api.the-odds-api.com/v4"

    def fetch_scores_for_sport(self, sport_name: str, days_from: int = 3) -> list[dict]:
        """Fetch completed events for active leagues in the given sport."""
        global _SCORES_QUOTA_EXHAUSTED_UNTIL
        if not self.api_key:
            return []

        now = time.time()
        if (
            _SCORES_QUOTA_EXHAUSTED_UNTIL
            and _SCORES_QUOTA_EXHAUSTED_UNTIL[0] == self.api_key
            and now < _SCORES_QUOTA_EXHAUSTED_UNTIL[1]
        ):
            return []

        import httpx

        from ..core.models import Sport
        from ..sources.the_odds_api import _SPORT_KEYS, _get_active_sport_keys

        try:
            sport_enum = Sport(sport_name)
        except ValueError:
            return []

        sport_keys = _SPORT_KEYS.get(sport_enum, [])
        if not sport_keys:
            return []

        all_events: list[dict] = []
        with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
            active_keys = _get_active_sport_keys(client, self.api_key)
            if active_keys:
                sport_keys = [k for k in sport_keys if k in active_keys]
            if not sport_keys:
                return []

            for key in sport_keys:
                cache_key = f"{key}:{days_from}"
                cache_hit = _SCORES_CACHE.get(cache_key)
                if cache_hit and (time.time() - cache_hit[0]) < _SCORES_CACHE_TTL:
                    all_events.extend(cache_hit[1])
                    continue
                try:
                    resp = client.get(
                        f"/sports/{key}/scores/",
                        params={"apiKey": self.api_key, "daysFrom": days_from},
                    )
                    rem = resp.headers.get("x-requests-remaining")
                    if rem is not None and str(rem).isdigit() and int(rem) <= 0:
                        _SCORES_QUOTA_EXHAUSTED_UNTIL = (self.api_key, time.time() + 300.0)
                    if resp.status_code == 200:
                        events = resp.json()
                        _SCORES_CACHE[cache_key] = (time.time(), events)
                        all_events.extend(events)
                    elif resp.status_code in (401, 429):
                        _SCORES_QUOTA_EXHAUSTED_UNTIL = (self.api_key, time.time() + 300.0)
                        log.warning("odds_api_scores_rate_limited", status=resp.status_code)
                        break
                except Exception as exc:
                    log.warning("odds_api_fetch_scores_error", sport_key=key, error=str(exc))
        return all_events

    @staticmethod
    def parse_scores(home_team: str, away_team: str, scores: list[dict]) -> tuple[float | None, float | None]:
        if not scores:
            return None, None
        home_score: float | None = None
        away_score: float | None = None
        for s in scores:
            name = (s.get("name") or "").strip()
            try:
                val = float(s.get("score", 0))
                if name.lower() == home_team.strip().lower():
                    home_score = val
                elif name.lower() == away_team.strip().lower():
                    away_score = val
            except (ValueError, TypeError):
                pass
        return home_score, away_score

    @classmethod
    def format_score(cls, home_team: str, away_team: str, scores: list[dict]) -> str | None:
        h, a = cls.parse_scores(home_team, away_team, scores)
        if h is None or a is None:
            return None
        h_str = str(int(h)) if float(h).is_integer() else f"{h:g}"
        a_str = str(int(a)) if float(a).is_integer() else f"{a:g}"
        return f"{h_str} - {a_str}"

    def determine_match_outcome(
        self,
        selection: str,
        home_team: str,
        away_team: str,
        scores: list[dict],
        market_type: str = "MATCH_ODDS",
    ) -> BetOutcome:
        """Evaluate outcome across 1X2, Totals (Over/Under), Handicap (Spreads), and BTTS."""
        home_score, away_score = self.parse_scores(home_team, away_team, scores)
        if home_score is None or away_score is None:
            return BetOutcome.PENDING

        sel = selection.strip()
        sel_lower = sel.lower()
        mt_upper = (market_type or "MATCH_ODDS").upper()

        # 1. Totals (Over / Under)
        ou_match = _OVER_UNDER_RE.search(sel)
        if mt_upper.startswith("TOTALS") or ou_match:
            if not ou_match:
                return BetOutcome.PENDING
            direction = ou_match.group(1).lower()
            line = float(ou_match.group(2))
            total = home_score + away_score
            if total == line:
                return BetOutcome.VOID
            if direction == "over":
                return BetOutcome.WON if total > line else BetOutcome.LOST
            return BetOutcome.WON if total < line else BetOutcome.LOST

        # 2. Handicap / Spreads — e.g. "Arsenal (-1.5)" or "Chelsea (+1.5)"
        hcap_match = _HANDICAP_SEL_RE.match(sel)
        if mt_upper.startswith("HANDICAP") or mt_upper.startswith("SPREAD") or hcap_match:
            if not hcap_match:
                return BetOutcome.PENDING
            team_part = hcap_match.group(1).strip().lower()
            line = float(hcap_match.group(2) or hcap_match.group(3))
            if team_part in home_team.lower() or home_team.lower() in team_part:
                adj = home_score + line
                opp = away_score
            elif team_part in away_team.lower() or away_team.lower() in team_part:
                adj = away_score + line
                opp = home_score
            else:
                return BetOutcome.PENDING
            if adj > opp:
                return BetOutcome.WON
            if adj < opp:
                return BetOutcome.LOST
            return BetOutcome.VOID

        # 3. Both Teams To Score (BTTS)
        if "BTTS" in mt_upper or "BOTH_TEAMS" in mt_upper:
            both_scored = home_score > 0 and away_score > 0
            if "yes" in sel_lower:
                return BetOutcome.WON if both_scored else BetOutcome.LOST
            if "no" in sel_lower:
                return BetOutcome.WON if not both_scored else BetOutcome.LOST
            return BetOutcome.PENDING

        # 4. Match Winner (1X2 / Moneyline)
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
            if is_draw:
                return BetOutcome.WON
            return BetOutcome.LOST
