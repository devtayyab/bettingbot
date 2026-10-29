"""Tests for result resolving, multi-market settlement, and event-isolated matching."""

from datetime import UTC, datetime, timedelta

from valuebet.config import Settings
from valuebet.core.models import MarketSnapshot, MarketStatus, Quote, Sport
from valuebet.core.results import BetOutcome, TheOddsAPIResultResolver
from valuebet.engine.matching import match_markets
from valuebet.engine.value_engine import ValueEngine


class _StaticSource:
    def __init__(self, name: str, snapshots: list[MarketSnapshot]) -> None:
        self.name = name
        self._snapshots = snapshots

    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        return self._snapshots


def test_determine_match_outcome_home_win():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "3"},
        {"name": "Chelsea", "score": "1"},
    ]
    assert resolver.format_score("Arsenal", "Chelsea", scores) == "3 - 1"
    assert resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", scores) == BetOutcome.WON
    assert resolver.determine_match_outcome("Chelsea", "Arsenal", "Chelsea", scores) == BetOutcome.LOST
    assert resolver.determine_match_outcome("Draw", "Arsenal", "Chelsea", scores) == BetOutcome.LOST


def test_determine_match_outcome_away_win():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "0"},
        {"name": "Chelsea", "score": "2"},
    ]
    assert resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", scores) == BetOutcome.LOST
    assert resolver.determine_match_outcome("Chelsea", "Arsenal", "Chelsea", scores) == BetOutcome.WON


def test_determine_match_outcome_draw():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "2"},
        {"name": "Chelsea", "score": "2"},
    ]
    assert resolver.determine_match_outcome("Draw", "Arsenal", "Chelsea", scores) == BetOutcome.WON
    assert resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", scores) == BetOutcome.LOST


def test_determine_match_outcome_totals_over_under_and_push():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "2"},
        {"name": "Chelsea", "score": "1"},
    ]
    assert resolver.determine_match_outcome("Over 2.5", "Arsenal", "Chelsea", scores, "TOTALS_2.5") == BetOutcome.WON
    assert resolver.determine_match_outcome("Under 2.5", "Arsenal", "Chelsea", scores, "TOTALS_2.5") == BetOutcome.LOST
    # Exact line 3.0 -> VOID (push)
    assert resolver.determine_match_outcome("Over 3.0", "Arsenal", "Chelsea", scores, "TOTALS_3.0") == BetOutcome.VOID


def test_determine_match_outcome_handicap_spreads():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "2"},
        {"name": "Chelsea", "score": "1"},
    ]
    # Arsenal won by 1 goal: Arsenal -1.5 loses, Chelsea +1.5 wins, Arsenal -1.0 pushes (VOID)
    # Supports both "Arsenal (-1.5)" (The Odds API format) and "Arsenal -1.5"
    assert resolver.determine_match_outcome("Arsenal (-1.5)", "Arsenal", "Chelsea", scores, "HANDICAP_-1.5") == BetOutcome.LOST
    assert resolver.determine_match_outcome("Arsenal -1.5", "Arsenal", "Chelsea", scores, "HANDICAP_-1.5") == BetOutcome.LOST
    assert resolver.determine_match_outcome("Chelsea +1.5", "Arsenal", "Chelsea", scores, "HANDICAP_-1.5") == BetOutcome.WON
    assert resolver.determine_match_outcome("Arsenal -1.0", "Arsenal", "Chelsea", scores, "HANDICAP_-1.0") == BetOutcome.VOID


def test_determine_match_outcome_pending_when_scores_missing():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    assert resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", []) == BetOutcome.PENDING


def test_totals_markets_do_not_cross_match_different_fixtures():
    now = datetime.now(UTC)
    kickoff = now + timedelta(hours=2)
    pinnacle_match_1 = MarketSnapshot(
        event_id="evt-1",
        event_name="Arsenal vs Chelsea",
        market_id="pin_evt-1_totals_2.5",
        market_type="TOTALS_2.5",
        sport=Sport.SOCCER,
        status=MarketStatus.PREMATCH,
        start_time=kickoff,
        quotes=[
            Quote(source="pinnacle", selection="Over 2.5", decimal_odds=1.95, captured_at=now),
            Quote(source="pinnacle", selection="Under 2.5", decimal_odds=1.95, captured_at=now),
        ],
    )
    target_match_2 = MarketSnapshot(
        event_id="evt-2",
        event_name="Liverpool vs Everton",
        market_id="stoix_evt-2_totals_2.5",
        market_type="TOTALS_2.5",
        sport=Sport.SOCCER,
        status=MarketStatus.PREMATCH,
        start_time=kickoff,
        quotes=[
            Quote(source="stoiximan", selection="Over 2.5", decimal_odds=2.30, captured_at=now),
            Quote(source="stoiximan", selection="Under 2.5", decimal_odds=1.65, captured_at=now),
        ],
    )
    # Different matches sharing generic "Over 2.5" / "Under 2.5" names MUST NOT match!
    assert match_markets(target_match_2, [pinnacle_match_1]) is None


def test_value_engine_pinnacle_fallback_for_totals_market():
    now = datetime.now(UTC)
    kickoff = now + timedelta(hours=2)
    pinnacle_totals = MarketSnapshot(
        event_id="evt-100999",
        event_name="Arsenal vs Chelsea",
        market_id="pin_evt-100999_totals_2.5",
        market_type="TOTALS_2.5",
        sport=Sport.SOCCER,
        status=MarketStatus.PREMATCH,
        start_time=kickoff,
        quotes=[
            Quote(source="pinnacle", selection="Over 2.5", decimal_odds=1.92, captured_at=now),
            Quote(source="pinnacle", selection="Under 2.5", decimal_odds=1.98, captured_at=now),
        ],
    )
    target_totals = MarketSnapshot(
        event_id="evt-100999",
        event_name="Arsenal vs Chelsea",
        market_id="stoix_evt-100999_totals_2.5",
        market_type="TOTALS_2.5",
        sport=Sport.SOCCER,
        status=MarketStatus.PREMATCH,
        start_time=kickoff,
        quotes=[
            Quote(source="stoiximan", selection="Over 2.5", decimal_odds=2.15, captured_at=now),
            Quote(source="stoiximan", selection="Under 2.5", decimal_odds=1.80, captured_at=now),
        ],
    )
    cfg = Settings(
        edge_threshold=0.02,
        enable_edge_buffer=False,
        require_confirmation=False,
        enable_pinnacle_confirmation=True,
        favorite_min_prob=0.15,
    )
    engine = ValueEngine(
        reference=_StaticSource("betfair", []),
        confirmation=_StaticSource("pinnacle", [pinnacle_totals]),
        targets=[_StaticSource("stoiximan", [target_totals])],
        settings=cfg,
    )
    res = engine.scan(Sport.SOCCER, live=False)
    assert len(res.signals) == 1
    assert res.signals[0].selection == "Over 2.5"
    assert res.signals[0].event_name == "Arsenal vs Chelsea"
    assert res.signals[0].market_type == "TOTALS_2.5"
