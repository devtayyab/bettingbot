"""Placement results must never imply a bet exists at the bookmaker when it does not.

These cover the reported failure: the app showed bets as placed while nothing was
on the platform.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from valuebet.db.models import Base, Signal
from valuebet.db.repository import get_event_exposure, pnl_summary, record_bet
from valuebet.placement.base import PlacementRequest, PlacementResult, PlacementStatus
from valuebet.placement.bet365 import Bet365Placer
from valuebet.placement.stoiximan import parse_money


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _signal(session, event_id=1, stake=5.0):
    sig = Signal(
        event_id=event_id, market_type="MATCH_ODDS", selection="Real Madrid",
        sport="soccer", fair_prob=0.6, confirm_prob=0.6, target_odds=1.9,
        edge=0.14, recommended_stake=stake, status="approved",
        detected_at=datetime.now(UTC),
    )
    session.add(sig)
    session.flush()
    return sig


def _result(**kw):
    base = dict(
        success=True, placed_odds=1.9, requested_stake=5.0, accepted_stake=5.0,
        dry_run=False, message="m",
    )
    base.update(kw)
    return PlacementResult(**base)


# --- the result contract ----------------------------------------------------

def test_dry_run_is_never_a_real_bet():
    r = _result(dry_run=True, status=PlacementStatus.DRY_RUN)
    assert r.success is True          # the slip was prepared fine...
    assert r.is_real_bet is False     # ...but nothing was staked
    assert r.verified_on_platform is False


def test_unconfirmed_counts_as_money_at_risk():
    r = _result(success=False, status=PlacementStatus.UNCONFIRMED)
    assert r.is_real_bet is True
    assert r.needs_manual_check is True


def test_verified_placement_is_the_only_success():
    r = _result(status=PlacementStatus.PLACED, verified_on_platform=True)
    assert r.is_real_bet and r.verified_on_platform and not r.needs_manual_check


def test_status_defaults_for_legacy_callers():
    assert _result().status == PlacementStatus.PLACED
    assert _result(dry_run=True).status == PlacementStatus.DRY_RUN
    assert _result(success=False).status == PlacementStatus.REJECTED


def test_stake_reduction_detected():
    r = _result(requested_stake=10.0, accepted_stake=2.0)
    assert r.was_stake_reduced is True


# --- persistence ------------------------------------------------------------

def test_dry_run_bet_marks_signal_paper_not_placed(session):
    sig = _signal(session)
    record_bet(session, sig, placed_odds=1.9, stake=5.0, dry_run=True, verified=False)
    assert sig.status == "paper"


def test_unverified_real_bet_marks_signal_unconfirmed(session):
    sig = _signal(session)
    record_bet(session, sig, placed_odds=1.9, stake=5.0, dry_run=False, verified=False)
    assert sig.status == "unconfirmed"


def test_verified_real_bet_marks_signal_placed(session):
    sig = _signal(session)
    record_bet(session, sig, placed_odds=1.9, stake=5.0, dry_run=False, verified=True)
    assert sig.status == "placed"


def test_dry_run_bets_excluded_from_pnl_and_exposure(session):
    sig = _signal(session)
    record_bet(session, sig, placed_odds=1.9, stake=5.0, dry_run=True, verified=False)
    summary = pnl_summary(session)
    assert summary["bets_total"] == 0
    assert summary["open_exposure"] == 0.0
    assert summary["paper_bets"] == 1
    # A paper bet must not eat into the real per-event exposure budget.
    assert get_event_exposure(session, sig.event_id) == 0.0


def test_real_bet_counted_in_exposure(session):
    sig = _signal(session)
    record_bet(session, sig, placed_odds=1.9, stake=5.0, dry_run=False, verified=True)
    assert get_event_exposure(session, sig.event_id) == 5.0
    assert pnl_summary(session)["open_exposure"] == 5.0


def test_record_bet_attributes_the_bookmaker(session):
    sig = _signal(session)
    bet = record_bet(session, sig, placed_odds=1.9, stake=5.0, dry_run=False,
                     bookmaker="bet365", verified=True)
    assert bet.book == "bet365"


# --- placers ----------------------------------------------------------------

def test_bet365_placer_reports_failure_not_fake_success():
    req = PlacementRequest(event_id="1", market_type="MATCH_ODDS", selection="X",
                           target_odds=2.0, stake=5.0, min_odds=1.9)
    res = Bet365Placer().place(req)
    assert res.success is False
    assert res.is_real_bet is False
    assert res.status == PlacementStatus.ERROR


@pytest.mark.parametrize("text,expected", [
    ("1,85", 1.85), ("1.85", 1.85), ("10,00 €", 10.0), ("€10.00", 10.0),
    ("1.234,56", 1234.56), ("", None), ("abc", None), (None, None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


# --- diagnostics -------------------------------------------------------------

def test_scan_tallies_drop_reasons():
    """The drop aggregate is the diagnostic for a scan that finds nothing."""
    from valuebet.core.models import Sport
    from valuebet.engine.value_engine import ValueEngine
    from valuebet.sources.mock import MockSource

    # Target prices are worse than the reference, so every selection fails on edge.
    ref = MockSource("betfair", {"1.1": [("Team A", 2.00), ("Draw", 3.4), ("Team B", 3.6)]})
    conf = MockSource("pinnacle", {"1.1": [("Team A", 2.00), ("Draw", 3.4), ("Team B", 3.6)]})
    tgt = MockSource("stoiximan", {"1.1": [("Team A", 1.50), ("Draw", 3.0), ("Team B", 3.0)]})

    engine = ValueEngine(ref, conf, [tgt])
    result = engine.scan(Sport.SOCCER, live=False)
    assert result.signals == []
    assert engine._drop_tally.get("edge_below_threshold", 0) > 0


def test_unmatched_market_is_reported_not_silent(capsys):
    """A name mismatch between feeds must be diagnosable, not a silent zero.

    Reads stdout rather than caplog: structlog is configured with a
    PrintLoggerFactory, so records never reach stdlib logging handlers.
    """
    from valuebet.core.models import Sport
    from valuebet.engine.value_engine import ValueEngine
    from valuebet.sources.mock import MockSource

    ref = MockSource("betfair", {"1.1": [("Manchester City", 1.66), ("Draw", 4.2), ("Arsenal FC", 6.0)]})
    conf = MockSource("pinnacle", {"1.1": [("Manchester City", 1.68), ("Draw", 4.1), ("Arsenal FC", 5.9)]})
    tgt = MockSource("stoiximan", {"1.1": [("Man City", 1.80), ("X", 4.0), ("Arsenal London", 5.5)]})

    result = ValueEngine(ref, conf, [tgt]).scan(Sport.SOCCER, live=False)
    out = capsys.readouterr().out

    assert result.signals == []
    # The funnel must attribute the loss to the matching stage, with an example.
    assert "target_scan_funnel" in out
    assert "dropped_no_reference_match=1" in out
    assert "arsenal london" in out
