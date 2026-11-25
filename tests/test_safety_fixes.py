"""Regression tests for the money-safety fixes: price floor, signal claiming,
event-id hashing, real-vs-paper P&L, paper simulation gating and dashboard auth."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from valuebet.config import reset_settings_cache
from valuebet.db.models import Base, Bet, Signal
from valuebet.db.repository import (
    _safe_event_id,
    claim_signal,
    pnl_summary,
    record_bet,
    settle_pending_bets,
)
from valuebet.engine.executor import min_acceptable_odds


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


def _signal(session, **kw) -> Signal:
    fields = dict(
        event_id=77, event_name="Alpha vs Beta", market_type="MATCH_ODDS",
        selection="Alpha", sport="handball", fair_prob=0.5, target_odds=2.1,
        edge=0.05, recommended_stake=5.0, status="approved",
    )
    fields.update(kw)
    sig = Signal(**fields)
    session.add(sig)
    session.flush()
    return sig


def test_price_floor_never_gives_away_the_edge():
    # 2% edge at 2.04: a flat 2% tolerance would accept 2.0, which is break-even.
    sig = SimpleNamespace(target_odds=2.04, fair_prob=0.5, edge=0.02)
    floor = min_acceptable_odds(sig)
    assert floor > 2.0
    assert 0.5 * floor - 1.0 >= 0.01 - 1e-9  # at least half the edge survives
    assert floor <= sig.target_odds


def test_price_floor_uses_slippage_when_edge_is_large():
    sig = SimpleNamespace(target_odds=2.4, fair_prob=0.5, edge=0.20)
    assert min_acceptable_odds(sig) == pytest.approx(2.4 * 0.98, abs=1e-3)


def test_signal_can_only_be_claimed_once(session):
    sig = _signal(session)
    session.commit()
    assert claim_signal(session, sig.id, "approved") is True
    assert claim_signal(session, sig.id, "approved") is False
    assert session.get(Signal, sig.id).status == "placing"


def test_goldedge_event_ids_do_not_collapse_to_their_digits():
    # Both ids contain the single digit "1"; they used to map to the same event.
    assert _safe_event_id("ge_abcdef1abc") != _safe_event_id("ge_1bcdefabcd")
    assert _safe_event_id("1.234567") == 1234567


def test_pnl_headline_is_real_money_and_paper_is_separate(session):
    real = _signal(session, selection="Alpha")
    paper = _signal(session, selection="Beta", event_id=78)
    b_real = record_bet(session, real, placed_odds=2.0, stake=5.0, dry_run=False, verified=True)
    b_paper = record_bet(session, paper, placed_odds=2.0, stake=4.0, dry_run=True)
    b_real.outcome, b_real.profit = "won", 5.0
    b_paper.outcome, b_paper.profit = "lost", -4.0
    session.flush()

    summary = pnl_summary(session)
    assert summary["bets_total"] == 1
    assert summary["realised_pnl"] == 5.0
    assert summary["paper_bets"] == 1
    assert summary["paper_realised_pnl"] == -4.0
    assert summary["paper_roi"] == -1.0


def test_paper_bets_are_not_settled_by_simulation_unless_enabled(session, monkeypatch):
    # "handball" has no score source, so only the simulation could settle it.
    sig = _signal(session, event_start_time=datetime.now(UTC) - timedelta(hours=5))
    bet = record_bet(session, sig, placed_odds=2.1, stake=5.0, dry_run=True)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")

    monkeypatch.setenv("PAPER_SIMULATE_OUTCOMES", "false")
    reset_settings_cache()
    assert settle_pending_bets(session) == 0
    assert session.get(Bet, bet.id).outcome == "pending"

    monkeypatch.setenv("PAPER_SIMULATE_OUTCOMES", "true")
    reset_settings_cache()
    assert settle_pending_bets(session) == 1
    assert "paper_simulation" in session.get(Bet, bet.id).note

    monkeypatch.undo()
    reset_settings_cache()


def test_dashboard_auth_enforced_when_configured(monkeypatch):
    from valuebet.api.main import _basic_auth_ok

    token = base64.b64encode(b"admin:s3cret").decode()
    assert _basic_auth_ok(f"Basic {token}", "admin", "s3cret")
    assert not _basic_auth_ok(f"Basic {token}", "admin", "other")
    assert not _basic_auth_ok(None, "admin", "s3cret")
    assert not _basic_auth_ok("Basic not-base64!!", "admin", "s3cret")
