"""Tests for BetfairStreamSource."""

from datetime import UTC, datetime

from valuebet.core.models import MarketStatus, Sport
from valuebet.sources.base import OddsSource
from valuebet.sources.betfair_stream import BetfairStreamSource
from valuebet.sources.mock import MockSource


def test_betfair_stream_protocol_and_fallback():
    """Verify BetfairStreamSource adheres to OddsSource protocol and falls back cleanly."""
    mock_source = MockSource("betfair", {"1.100": [("Team A", 1.80), ("Team B", 2.20)]})
    stream = BetfairStreamSource(mock_source)

    # Check protocol adherence
    assert isinstance(stream, OddsSource)
    assert stream.name == "betfair_stream"

    # When not running, fetch_markets delegates to fallback
    snaps = stream.fetch_markets(Sport.SOCCER, live=False)
    assert len(snaps) > 0
    assert snaps[0].market_id == "1.100"


def test_betfair_stream_cache_parsing():
    """Verify market cache parsing with MarketBookCache structures."""
    try:
        from betfairlightweight.streaming.cache import MarketBookCache
    except ImportError:
        return

    mock_source = MockSource("betfair", {})
    stream = BetfairStreamSource(mock_source)
    stream._is_running = True

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    cache = MarketBookCache("1.9999", now_ms, False, False, False)
    cache._process_market_definition({
        "betDelay": 0,
        "bettingType": "ODDS",
        "bspMarket": False,
        "bspReconciled": False,
        "complete": True,
        "crossMatching": False,
        "discountAllowed": False,
        "eventId": "evt-123",
        "eventTypeId": "1",
        "inPlay": True,
        "marketBaseRate": 5.0,
        "marketTime": "2026-09-14T15:00:00.000Z",
        "numberOfActiveRunners": 2,
        "numberOfWinners": 1,
        "persistenceEnabled": False,
        "regulators": "MR_INT",
        "runnersVoidable": False,
        "status": "OPEN",
        "timezone": "UTC",
        "turnInPlayEnabled": True,
        "version": 1,
        "runners": [
            {"id": 1001, "name": "Arsenal", "sortPriority": 1, "status": "ACTIVE"},
            {"id": 1002, "name": "Chelsea", "sortPriority": 2, "status": "ACTIVE"},
        ],
    })

    runner1 = cache.runner_dict[(1001, 0)]
    runner1.available_to_back.update([[2.05, 1500.0]], True)
    runner1.available_to_lay.update([[2.08, 2000.0]], True)

    runner2 = cache.runner_dict[(1002, 0)]
    runner2.available_to_back.update([[3.80, 800.0]], True)
    runner2.available_to_lay.update([[3.85, 1200.0]], True)

    stream._market_caches = {"1.9999": cache}

    # Fetch live markets
    live_snaps = stream.fetch_markets(Sport.SOCCER, live=True)
    assert len(live_snaps) == 1
    snap = live_snaps[0]

    assert snap.event_id == "evt-123"
    assert snap.market_id == "1.9999"
    assert snap.status == MarketStatus.LIVE
    assert len(snap.quotes) == 2

    q_arsenal = next(q for q in snap.quotes if q.selection == "Arsenal")
    assert q_arsenal.decimal_odds == 2.05
    assert q_arsenal.lay_odds == 2.08
    assert q_arsenal.back_liquidity == 1500.0
    assert q_arsenal.lay_liquidity == 2000.0
    assert isinstance(q_arsenal.captured_at, datetime)

    # When live=False is requested for in-play market, should return empty
    prematch_snaps = stream.fetch_markets(Sport.SOCCER, live=False)
    assert len(prematch_snaps) == 0
