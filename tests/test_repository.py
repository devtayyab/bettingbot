"""Repository tests: deterministic event ids and signal de-duplication (SQLite)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from valuebet.core.models import Sport, ValueSignal
from valuebet.db.models import Base, Signal
from valuebet.db.repository import _safe_event_id, save_signal


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _signal(selection="Team A", edge=0.07):
    return ValueSignal(
        event_id="evt-abc", market_id="m1", market_type="MATCH_ODDS",
        selection=selection, sport=Sport.SOCCER, fair_prob=0.6, confirm_prob=0.59,
        target_bookmaker="stoiximan",
        target_odds=1.8, edge=edge, recommended_stake=10.0,
        detected_at=datetime.now(timezone.utc),
    )


def test_event_id_is_deterministic_for_non_numeric_ids():
    # Must be stable across calls/processes (no salted builtin hash()).
    assert _safe_event_id("evt-abc") == _safe_event_id("evt-abc")
    assert _safe_event_id("evt-abc") != _safe_event_id("evt-xyz")


def test_event_id_extracts_digits_from_betfair_ids():
    assert _safe_event_id("1.234567") == 1234567


def test_save_signal_dedupes_open_signals(session):
    first = save_signal(session, _signal())
    session.flush()
    assert first is not None

    # Same opportunity on the next poll must NOT create a second row.
    second = save_signal(session, _signal(edge=0.08))
    assert second is None
    assert len(list(session.scalars(select(Signal)))) == 1


def test_save_signal_allows_new_after_resolution(session):
    first = save_signal(session, _signal())
    session.flush()
    first.status = "placed"   # no longer open
    session.flush()

    # A fresh detection after the prior one resolved is allowed.
    again = save_signal(session, _signal())
    assert again is not None
    assert len(list(session.scalars(select(Signal)))) == 2
