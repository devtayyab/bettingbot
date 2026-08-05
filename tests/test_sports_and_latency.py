"""Unit tests for multi-sport coverage, market size (volume & liquidity) health checks, and latency controls."""

from datetime import datetime, timedelta, timezone
from valuebet.config import Settings
from valuebet.core.models import MarketSnapshot, MarketStatus, Quote, Sport
from valuebet.engine.value_engine import ValueEngine
from valuebet.sources.mock import MockSource, demo_sources


def test_all_sports_supported_in_engine():
    betfair, pinnacle, stoiximan = demo_sources()
    engine = ValueEngine(betfair, pinnacle, [stoiximan])
    
    for sport in Sport:
        result = engine.scan(sport, live=False)
        assert isinstance(result.snapshots, list)
        assert len(result.snapshots) > 0


def test_market_size_volume_rejection():
    # Betfair total_matched is 100, below min_total_matched of 1000
    betfair = MockSource("betfair", {"1.111": [("Team A", 1.66), ("Draw", 4.2), ("Team B", 6.0)]})
    pinnacle = MockSource("pinnacle", {"1.111": [("Team A", 1.68), ("Draw", 4.1), ("Team B", 5.9)]})
    stoiximan = MockSource("stoiximan", {"1.111": [("Team A", 1.80), ("Draw", 4.0), ("Team B", 5.5)]})
    
    settings = Settings(
        sport_overrides={
            "soccer": {"min_total_matched": 1000.0}
        }
    )
    
    # Custom source returning snapshot with low volume
    class LowVolumeMock(MockSource):
        def fetch_markets(self, sport, live=False):
            snaps = super().fetch_markets(sport, live)
            return [
                MarketSnapshot(
                    event_id=s.event_id,
                    market_id=s.market_id,
                    market_type=s.market_type,
                    sport=s.sport,
                    status=s.status,
                    start_time=s.start_time,
                    total_matched=100.0,  # Below 1000 threshold
                    quotes=s.quotes,
                )
                for s in snaps
            ]

    engine = ValueEngine(LowVolumeMock("betfair", betfair._odds_table), pinnacle, [stoiximan], settings=settings)
    res = engine.scan(Sport.SOCCER)
    assert res.signals == [], "Expected signals to be rejected due to low total matched volume"


def test_market_liquidity_rejection():
    # Target quote back_liquidity is 2.0, below min_liquidity of 10.0
    now = datetime.now(timezone.utc)
    ref_snap = MarketSnapshot(
        event_id="evt-1",
        market_id="m1",
        market_type="MATCH_ODDS",
        sport=Sport.BASKETBALL,
        status=MarketStatus.PREMATCH,
        start_time=now + timedelta(hours=1),
        total_matched=5000.0,
        quotes=[
            Quote("betfair", "Team A", 1.50, now, back_liquidity=1000.0, lay_odds=1.52, lay_liquidity=1000.0),
            Quote("betfair", "Team B", 3.00, now, back_liquidity=1000.0, lay_odds=3.05, lay_liquidity=1000.0),
        ]
    )
    
    tgt_snap = MarketSnapshot(
        event_id="evt-1",
        market_id="m1",
        market_type="MATCH_ODDS",
        sport=Sport.BASKETBALL,
        status=MarketStatus.PREMATCH,
        start_time=now + timedelta(hours=1),
        total_matched=5000.0,
        quotes=[
            Quote("stoiximan", "Team A", 1.80, now, back_liquidity=2.0),  # Low liquidity!
            Quote("stoiximan", "Team B", 2.80, now, back_liquidity=2.0),
        ]
    )
    
    class SingleMock:
        def __init__(self, name, snap):
            self.name = name
            self.snap = snap
        def fetch_markets(self, sport, live=False):
            return [self.snap]

    settings = Settings(
        sport_overrides={
            "basketball": {"min_liquidity": 10.0, "edge_threshold": 0.01}
        }
    )
    
    engine = ValueEngine(SingleMock("betfair", ref_snap), SingleMock("pinnacle", ref_snap), [SingleMock("stoiximan", tgt_snap)], settings=settings)
    res = engine.scan(Sport.BASKETBALL)
    assert res.signals == [], "Expected signals to be rejected due to low target liquidity"


def test_stale_odds_latency_rejection():
    # Quote captured 10 seconds ago, max_live_latency_seconds is 3.0s
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(seconds=10)
    
    ref_snap = MarketSnapshot(
        event_id="evt-1",
        market_id="m1",
        market_type="MATCH_ODDS",
        sport=Sport.TENNIS,
        status=MarketStatus.LIVE,
        start_time=now,
        total_matched=5000.0,
        quotes=[
            Quote("betfair", "Player 1", 1.50, now, back_liquidity=1000.0, lay_odds=1.52, lay_liquidity=1000.0),
            Quote("betfair", "Player 2", 3.00, now, back_liquidity=1000.0, lay_odds=3.05, lay_liquidity=1000.0),
        ]
    )
    
    tgt_snap = MarketSnapshot(
        event_id="evt-1",
        market_id="m1",
        market_type="MATCH_ODDS",
        sport=Sport.TENNIS,
        status=MarketStatus.LIVE,
        start_time=now,
        total_matched=5000.0,
        quotes=[
            Quote("stoiximan", "Player 1", 1.80, stale_time, back_liquidity=500.0),  # Stale!
            Quote("stoiximan", "Player 2", 2.80, stale_time, back_liquidity=500.0),
        ]
    )
    
    class SingleMock:
        def __init__(self, name, snap):
            self.name = name
            self.snap = snap
        def fetch_markets(self, sport, live=False):
            return [self.snap]

    settings = Settings(
        sport_overrides={
            "tennis": {"max_live_latency_seconds": 3.0}
        }
    )
    
    engine = ValueEngine(SingleMock("betfair", ref_snap), SingleMock("pinnacle", ref_snap), [SingleMock("stoiximan", tgt_snap)], settings=settings)
    res = engine.scan(Sport.TENNIS, live=True)
    assert res.signals == [], "Expected live signal to be rejected due to stale latency (>3.0s)"
