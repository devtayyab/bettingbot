"""Tests for PropLine odds/scores adapter, American-to-decimal conversion, and 24h daily quota pacing."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from valuebet.config import active_odds_provider, reset_settings_cache
from valuebet.core.models import Sport
from valuebet.core.results import BetOutcome, TheOddsAPIResultResolver
from valuebet.db.models import Base, Bet, Signal
from valuebet.db.repository import record_bet, settle_pending_bets
from valuebet.sources import the_odds_api as oa
from valuebet.sources.the_odds_api import (
    TheOddsAPISource,
    _canonical_team_name,
    _propline_pacing_wait,
    _to_decimal_odds,
    _update_daily_pacing,
)


def test_to_decimal_odds_converts_american_and_preserves_decimal():
    assert _to_decimal_odds(135) == pytest.approx(2.35)
    assert _to_decimal_odds(100) == pytest.approx(2.0)
    assert _to_decimal_odds(-150) == pytest.approx(1.6667, abs=1e-4)
    assert _to_decimal_odds(-100) == pytest.approx(2.0)
    assert _to_decimal_odds(2.45) == pytest.approx(2.45)
    assert _to_decimal_odds(1.0) is None
    assert _to_decimal_odds(None) is None


def test_canonical_team_name_maps_abbreviations():
    assert _canonical_team_name("Man Utd", "Manchester United", "Brentford FC") == "Manchester United"
    assert _canonical_team_name("Draw", "Manchester United", "Brentford FC") == "Draw"
    assert _canonical_team_name("Tie", "Manchester United", "Brentford FC") == "Draw"
    assert _canonical_team_name("Brentford", "Manchester United", "Brentford FC") == "Brentford FC"


def test_active_odds_provider_prefers_propline_when_key_set(monkeypatch):
    monkeypatch.setenv("PROPLINE_API_KEY", "pl_test_key")
    monkeypatch.setenv("ODDS_PROVIDER", "auto")
    reset_settings_cache()
    prov, key = active_odds_provider()
    assert prov == "propline"
    assert key == "pl_test_key"

    monkeypatch.setenv("ODDS_PROVIDER", "the_odds_api")
    monkeypatch.setenv("THE_ODDS_API_KEY", "oa_test_key")
    reset_settings_cache()
    prov, key = active_odds_provider()
    assert prov == "the_odds_api"
    assert key == "oa_test_key"

    monkeypatch.undo()
    reset_settings_cache()


def test_propline_daily_pacing_reserves_scores_quota_and_stretches():
    oa._QUOTA_EXHAUSTED_UNTIL = None
    oa._DAILY_QUOTA_STATE = None
    oa._LAST_NETWORK_FETCH.clear()

    now = time.time()
    reset_ts = str(int(now + 43200))  # 12 hours left in the day
    # Simulate tight remaining quota (e.g. only 120 left for 12 hours -> need to space out fetches)
    _update_daily_pacing(
        "k1",
        {"x-daily-limit": "1000", "x-daily-remaining": "120", "x-daily-used": "880", "x-daily-reset": reset_ts},
        is_propline=True,
        reserve=25,
    )
    oa._LAST_NETWORK_FETCH["pl:soccer_epl:h2h"] = now - 60.0  # fetched 1m ago
    assert _propline_pacing_wait("k1", "pl:soccer_epl:h2h", num_leagues=8) is True

    # Simulate hitting reserve (<= 25): triggers pause for odds scans to save reserve for scores
    _update_daily_pacing(
        "k1",
        {"x-daily-limit": "1000", "x-daily-remaining": "25", "x-daily-used": "975", "x-daily-reset": reset_ts},
        is_propline=True,
        reserve=25,
    )
    assert oa._QUOTA_EXHAUSTED_UNTIL is not None and oa._QUOTA_EXHAUSTED_UNTIL[0] == "k1"

    oa._QUOTA_EXHAUSTED_UNTIL = None
    oa._DAILY_QUOTA_STATE = None
    oa._LAST_NETWORK_FETCH.clear()


def test_propline_fetch_markets_parses_american_and_multimarket(monkeypatch):
    monkeypatch.setenv("PROPLINE_API_KEY", "pl_key_123")
    monkeypatch.setenv("ODDS_PROVIDER", "propline")
    monkeypatch.setenv("ODDS_API_MARKETS", "h2h,totals,spreads")
    monkeypatch.setenv("ODDS_API_LEAGUES", "soccer_epl")
    reset_settings_cache()
    oa._ODDS_CACHE.clear()
    oa._ACTIVE_SPORTS_CACHE = ("propline", time.time(), {"soccer_epl"})
    oa._QUOTA_EXHAUSTED_UNTIL = None
    oa._DAILY_QUOTA_STATE = None

    sample_events = [
        {
            "id": "720510",
            "sport_key": "soccer_epl",
            "home_team": "Manchester United",
            "away_team": "Brentford FC",
            "commence_time": "2099-11-28T16:00:00Z",
            "live": False,
            "bookmakers": [
                {
                    "key": "stoiximan",
                    "markets": [
                        {
                            "key": "h2h",
                            "suspended_at": None,
                            "outcomes": [
                                {"name": "Man Utd", "price": 135},
                                {"name": "Draw", "price": 240},
                                {"name": "Brentford FC", "price": 210},
                            ],
                        },
                        {
                            "key": "totals",
                            "suspended_at": None,
                            "outcomes": [
                                {"name": "Over", "price": -110, "point": 2.5},
                                {"name": "Under", "price": -110, "point": 2.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "suspended_at": None,
                            "outcomes": [
                                {"name": "Over", "price": 137, "point": 3.5},
                                {"name": "Under", "price": -165, "point": 3.5},
                            ],
                        },
                        {
                            "key": "spreads",
                            "suspended_at": None,
                            "outcomes": [
                                {"name": "Man Utd", "price": -128, "point": 0.0},
                                {"name": "Brentford FC", "price": 105, "point": 0.0},
                            ],
                        },
                    ],
                }
            ],
        }
    ]

    class FakeResp:
        status_code = 200
        headers = {"x-daily-limit": "1000", "x-daily-remaining": "990", "x-daily-used": "10"}
        text = "ok"

        def json(self):
            return sample_events

    src = TheOddsAPISource(target_bookmaker="stoiximan", name="stoiximan")
    monkeypatch.setattr(src._client, "get", lambda *a, **kw: FakeResp())

    snaps = src.fetch_markets(Sport.SOCCER, live=False)
    types = {s.market_type for s in snaps}
    assert {"MATCH_ODDS", "TOTALS_2.5", "TOTALS_3.5", "HANDICAP_0"} <= types
    h2h = next(s for s in snaps if s.market_type == "MATCH_ODDS")
    q_map = {q.selection: q.decimal_odds for q in h2h.quotes}
    assert q_map["Manchester United"] == pytest.approx(2.35)
    assert q_map["Draw"] == pytest.approx(3.40)
    assert q_map["Brentford FC"] == pytest.approx(3.10)

    oa._ODDS_CACHE.clear()
    oa._ACTIVE_SPORTS_CACHE = None
    monkeypatch.undo()
    reset_settings_cache()


def test_propline_scores_normalization_and_settlement(monkeypatch):
    from valuebet.core import results as res_mod

    monkeypatch.setenv("PROPLINE_API_KEY", "pl_key_123")
    monkeypatch.setenv("ODDS_PROVIDER", "propline")
    monkeypatch.setenv("ODDS_API_LEAGUES", "soccer_epl")
    reset_settings_cache()
    res_mod._SCORES_CACHE.clear()
    oa._ACTIVE_SPORTS_CACHE = ("propline", time.time(), {"soccer_epl"})

    class FakeScoresResp:
        status_code = 200
        headers = {"x-daily-remaining": "988"}

        def json(self):
            return [
                {
                    "id": "720520",
                    "sport_key": "soccer_epl",
                    "home_team": "Nottingham Forest",
                    "away_team": "Chelsea",
                    "commence_time": "2020-01-01T20:00:00Z",
                    "status": "final",
                    "home_score": 3,
                    "away_score": 2,
                }
            ]

    import httpx
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **kw: FakeScoresResp())
    # Skip ESPN so we exercise PropLine score resolver cleanly
    monkeypatch.setattr(res_mod.ESPNResultResolver, "fetch_scores_for_sport", lambda self, sp: [])

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        sig = Signal(
            event_id=720520,
            event_name="Nottingham Forest vs Chelsea",
            market_type="TOTALS_3.5",
            selection="Over 3.5",
            sport="soccer",
            fair_prob=0.5,
            target_odds=2.37,
            edge=0.18,
            recommended_stake=10.0,
            event_start_time=datetime(2020, 1, 1, 20, 0, tzinfo=UTC),
        )
        session.add(sig)
        session.flush()
        bet = record_bet(session, sig, placed_odds=2.37, stake=10.0, dry_run=True)

        assert settle_pending_bets(session) == 1
        assert bet.outcome == BetOutcome.WON.value
        assert bet.final_score == "3 - 2"
        assert bet.profit == 13.7
        assert "auto_settled_by:propline" in bet.note

    res_mod._SCORES_CACHE.clear()
    oa._ACTIVE_SPORTS_CACHE = None
    monkeypatch.undo()
    reset_settings_cache()
