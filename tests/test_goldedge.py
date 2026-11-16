"""Tests for GoldEdge scraper and parser."""

from datetime import UTC, datetime

from valuebet.core.models import Sport
from valuebet.sources.goldedge import GoldEdgeSource


def test_goldedge_parse_row_text():
    source = GoldEdgeSource(headless=True)
    now = datetime.now(UTC)

    # Realistic sample row text from GoldEdge valuebets table
    sample_text = """
    Arsenal vs Chelsea
    Arsenal
    Betano
    2.25
    Pinnacle: 2.10
    +4.2%
    """

    sig = source._parse_row_text(sample_text, idx=1, now=now, min_edge=0.02)
    assert sig is not None
    assert sig.selection == "Arsenal"
    assert sig.target_odds == 2.25
    assert sig.edge >= 0.04
    assert sig.fair_prob > 0.4
    assert sig.recommended_stake > 0
    assert sig.sport == Sport.SOCCER
    assert sig.betfair_odds is None
    assert sig.pinnacle_odds == 2.10


def test_goldedge_rejects_sub_threshold():
    source = GoldEdgeSource(headless=True)
    now = datetime.now(UTC)

    # Low edge sample
    sample_text = """
    Barcelona vs Real Madrid
    Barcelona
    Betano
    2.05
    Pinnacle: 2.04
    +0.5%
    """

    sig = source._parse_row_text(sample_text, idx=2, now=now, min_edge=0.02)
    assert sig is None


def test_goldedge_open_valuebet_card_betano_vs_pinnacle():
    source = GoldEdgeSource(headless=True)
    now = datetime.now(UTC)

    card_text = """
    +7.09%
    EV
    3d ago
    Netherlands · Eerste Divisie
    MVV Maastricht
    Vitesse Arnhem
    15/11 15:30 · in 3d 5h
    MVV Maastricht
    Draw No Bet · Home
    STRONG
    Betano
    2.45
    ▼ 15.5%
    KELLY 1.7%
    FAIR ODDS
    2.24
    Pin 2.24 · vig 0.0%
    SHARPS
    Pinnacle -31.7%
    """

    sig = source._parse_valuebet_card(card_text, idx=0, now=now, min_edge=0.015)
    assert sig is not None
    assert sig.event_name == "MVV Maastricht vs Vitesse Arnhem"
    assert sig.selection == "MVV Maastricht"
    assert sig.target_bookmaker == "stoiximan"
    assert sig.target_odds == 2.45
    assert sig.pinnacle_odds == 2.24
    assert sig.betfair_odds is None
    assert sig.edge == 0.0709


def test_goldedge_rejects_negative_edge_and_other_bookies():
    source = GoldEdgeSource(headless=True)
    now = datetime.now(UTC)

    neg_card = """
    -2.50%
    EV
    MVV Maastricht
    Vitesse Arnhem
    MVV Maastricht
    Betano
    2.10
    FAIR ODDS
    2.24
    """
    assert source._parse_valuebet_card(neg_card, idx=0, now=now, min_edge=0.015) is None

    other_bookie = """
    +5.00%
    EV
    MVV Maastricht
    Vitesse Arnhem
    MVV Maastricht
    10Bet
    2.45
    FAIR ODDS
    2.24
    """
    assert source._parse_valuebet_card(other_bookie, idx=0, now=now, min_edge=0.015) is None


def test_match_bet_conflict_prevents_duplicates_and_opposites():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from valuebet.db.models import Base, Bet, Signal
    from valuebet.db.repository import check_match_bet_conflict

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        existing_sig = Signal(
            event_id=1001,
            event_name="MVV Maastricht vs Vitesse Arnhem",
            market_type="DRAW_NO_BET",
            selection="MVV Maastricht",
            sport="soccer",
            fair_prob=0.4464,
            confirm_prob=0.4464,
            target_odds=2.45,
            edge=0.0709,
            recommended_stake=20.0,
            status="paper",
        )
        session.add(existing_sig)
        session.flush()
        session.add(
            Bet(
                signal_id=existing_sig.id,
                book="stoiximan",
                selection="MVV Maastricht",
                placed_odds=2.45,
                requested_stake=20.0,
                stake=20.0,
                outcome="pending",
                dry_run=True,
            )
        )
        session.flush()

        # 1. Exact duplicate -> rejected
        dup_sig = Signal(
            event_id=1001,
            event_name="MVV Maastricht vs Vitesse Arnhem",
            market_type="DRAW_NO_BET",
            selection="MVV Maastricht",
            sport="soccer",
            fair_prob=0.45,
            target_odds=2.50,
            edge=0.08,
            recommended_stake=20.0,
        )
        assert check_match_bet_conflict(session, dup_sig, dry_run=True) == "duplicate_bet"

        # 2. Opposite team -> rejected
        opp_sig = Signal(
            event_id=1001,
            event_name="MVV Maastricht vs Vitesse Arnhem",
            market_type="h2h",
            selection="Vitesse Arnhem",
            sport="soccer",
            fair_prob=0.35,
            target_odds=3.10,
            edge=0.05,
            recommended_stake=15.0,
        )
        assert check_match_bet_conflict(session, opp_sig, dry_run=True) == "opposite_bet_conflict"

        # 3. Different independent market (Over 2.5) -> allowed!
        tot_sig = Signal(
            event_id=1001,
            event_name="MVV Maastricht vs Vitesse Arnhem",
            market_type="totals_2.5",
            selection="Over 2.5",
            sport="soccer",
            fair_prob=0.55,
            target_odds=1.95,
            edge=0.07,
            recommended_stake=20.0,
        )
        assert check_match_bet_conflict(session, tot_sig, dry_run=True) is None

        # Place Over 2.5, then test Under 2.5 -> rejected!
        session.add(tot_sig)
        session.flush()
        session.add(
            Bet(
                signal_id=tot_sig.id,
                book="stoiximan",
                selection="Over 2.5",
                placed_odds=1.95,
                requested_stake=20.0,
                stake=20.0,
                outcome="pending",
                dry_run=True,
            )
        )
        session.flush()

        under_sig = Signal(
            event_id=1001,
            event_name="MVV Maastricht vs Vitesse Arnhem",
            market_type="totals_2.5",
            selection="Under 2.5",
            sport="soccer",
            fair_prob=0.50,
            target_odds=2.10,
            edge=0.05,
            recommended_stake=15.0,
        )
        assert check_match_bet_conflict(session, under_sig, dry_run=True) == "opposite_bet_conflict"

