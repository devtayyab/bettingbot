"""Repository tests: deterministic event ids, 1.5% re-read rule, and demo data cleanup."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from valuebet.core.models import Sport, ValueSignal
from valuebet.db.models import Base, Bet, Signal
from valuebet.db.repository import _safe_event_id, purge_demo_data, save_signal


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


def _signal(selection="Arsenal", edge=0.07, event_id="evt-abc", event_name="Arsenal vs Chelsea", market_type="MATCH_ODDS"):
    return ValueSignal(
        event_id=event_id,
        event_name=event_name,
        market_id="m1",
        market_type=market_type,
        selection=selection,
        sport=Sport.SOCCER,
        fair_prob=0.6,
        confirm_prob=0.59,
        target_bookmaker="stoiximan",
        target_odds=1.8,
        edge=edge,
        recommended_stake=10.0,
        detected_at=datetime.now(UTC),
    )


def test_event_id_is_deterministic_for_non_numeric_ids():
    assert _safe_event_id("evt-abc") == _safe_event_id("evt-abc")
    assert _safe_event_id("evt-abc") != _safe_event_id("evt-xyz")


def test_event_id_extracts_digits_from_betfair_ids():
    assert _safe_event_id("1.234567") == 1234567


def test_save_signal_dedupes_open_signals_below_1_5_percent(session):
    first = save_signal(session, _signal(edge=0.07))
    session.flush()
    assert first is not None
    assert first.event_name == "Arsenal vs Chelsea"

    # +1.0% edge change (< 1.5% threshold) on the next poll must be skipped
    second = save_signal(session, _signal(edge=0.08))
    assert second is None
    assert len(list(session.scalars(select(Signal)))) == 1


def test_save_signal_updates_open_signal_when_edge_changes_at_least_1_5_percent(session):
    first = save_signal(session, _signal(edge=0.07))
    session.flush()
    assert first is not None

    # +1.6% edge change (>= 1.5%) while still "detected" updates the existing open signal
    updated = save_signal(session, _signal(edge=0.086))
    session.flush()
    assert updated is not None
    assert updated.id == first.id
    assert pytest.approx(updated.edge) == 0.086
    assert len(list(session.scalars(select(Signal)))) == 1


def test_save_signal_re_read_after_placement_requires_1_5_percent_delta(session):
    first = save_signal(session, _signal(edge=0.07), min_edge_delta=0.015)
    session.flush()
    assert first is not None
    first.status = "placed"  # already placed / paper-traded
    session.flush()

    # Exact same edge (0% change) or +1.0% change (< 1.5%) must NOT re-read/duplicate the bet
    assert save_signal(session, _signal(edge=0.07), min_edge_delta=0.015) is None
    assert save_signal(session, _signal(edge=0.08), min_edge_delta=0.015) is None
    assert len(list(session.scalars(select(Signal)))) == 1

    # >= 1.5% edge change (0.070 -> 0.086 = +1.6% delta) IS allowed to create a new signal row
    again = save_signal(session, _signal(edge=0.086), min_edge_delta=0.015)
    session.flush()
    assert again is not None
    assert again.id != first.id
    assert len(list(session.scalars(select(Signal)))) == 2


def test_purge_demo_data_removes_only_mock_rows(session):
    demo_sig = save_signal(
        session,
        _signal(selection="Team A", event_id="1001", event_name="Team A vs Team B", edge=0.06),
    )
    real_sig = save_signal(
        session,
        _signal(selection="Arsenal", event_id="99887766", event_name="Arsenal vs Chelsea", edge=0.06),
    )
    session.flush()
    assert demo_sig is not None and real_sig is not None

    session.add(
        Bet(
            signal_id=demo_sig.id,
            book="stoiximan",
            selection="Team A",
            placed_odds=2.05,
            requested_stake=10.0,
            stake=10.0,
            outcome="pending",
        )
    )
    session.add(
        Bet(
            signal_id=real_sig.id,
            book="stoiximan",
            selection="Arsenal",
            placed_odds=2.05,
            requested_stake=10.0,
            stake=10.0,
            outcome="pending",
        )
    )
    session.flush()

    res = purge_demo_data(session)
    assert res["signals_deleted"] == 1
    assert res["bets_deleted"] == 1

    remaining_signals = list(session.scalars(select(Signal)))
    assert len(remaining_signals) == 1
    assert remaining_signals[0].selection == "Arsenal"
