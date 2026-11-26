import pytest
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


def test_match_bet_conflict_prevents_duplicates_and_opposites(monkeypatch):
    from valuebet.config import reset_settings_cache

    monkeypatch.setenv("ALLOW_OPPOSITE_BETS", "false")
    reset_settings_cache()
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

        # With opposite bets allowed (the default) both sides may be backed,
        # but an exact duplicate is still refused.
        monkeypatch.setenv("ALLOW_OPPOSITE_BETS", "true")
        reset_settings_cache()
        assert check_match_bet_conflict(session, opp_sig, dry_run=True) is None
        assert check_match_bet_conflict(session, under_sig, dry_run=True) is None
        assert check_match_bet_conflict(session, dup_sig, dry_run=True) == "duplicate_bet"

    monkeypatch.undo()
    reset_settings_cache()


# --- GoldEdge odds-list rows (real page structure) and results -----------------

def _row(**kw):
    row = {
        "href": "/odds/football/europe/champions-league/como-aek-athens-38czyxt",
        "start": "2099-11-24T20:00:00.000Z",
        "live": False,
        "teams": ["Como", "AEK Athens"],
        "cells": [
            {"title": "Betano · fair 1.33", "odds": "1.30", "book": "Betano"},
            {"title": "Stake · fair 7.38", "odds": "6.80", "book": "Stake"},
            {"title": "Betano · fair 9.50", "odds": "10.3", "book": "Betano"},
        ],
    }
    row.update(kw)
    return row


def _parse(row, min_edge=0.02):
    from valuebet.sources.goldedge import parse_event_row

    return parse_event_row(
        row, now=datetime.now(UTC), min_edge=min_edge, stake_fn=lambda p, o: 5.0
    )


def test_goldedge_row_edge_is_measured_against_pinnacle_fair_price():
    sigs = _parse(_row())
    # Betano 1.30 is BELOW fair 1.33 (no value); Stake is not our book;
    # only Betano 10.3 vs fair 9.50 qualifies.
    assert [s.selection for s in sigs] == ["AEK Athens"]
    sig = sigs[0]
    assert sig.edge == round(10.3 / 9.50 - 1, 4)
    assert sig.fair_prob == round(1 / 9.50, 4)
    assert sig.pinnacle_odds == 9.50
    assert sig.event_name == "Como vs AEK Athens"
    assert sig.event_id == "ge_como-aek-athens-38czyxt"
    assert sig.source_url.endswith("/odds/football/europe/champions-league/como-aek-athens-38czyxt")
    assert sig.event_start_time is not None and sig.sport == Sport.SOCCER


def test_goldedge_row_skips_live_started_and_unpriced_rows():
    assert _parse(_row(live=True)) == []
    assert _parse(_row(start="2020-01-01T00:00:00.000Z")) == []
    no_fair = _row(cells=[
        {"title": "", "odds": "1.30", "book": "Betano"},
        {"title": "", "odds": "6.80", "book": "Betano"},
        {"title": "", "odds": "10.3", "book": "Betano"},
    ])
    assert _parse(no_fair) == []


def test_goldedge_result_only_when_finished():
    from valuebet.sources.goldedge import parse_event_result

    live = {"status_class": "evh-status s-live", "status_text": "● LIVE", "score_text": "2–\nto\n1"}
    assert parse_event_result(live, "A", "B") is None
    done = {"status_class": "evh-status s-finished", "status_text": "FINISHED",
            "score_text": "2–\nto\n0"}
    res = parse_event_result(done, "Como", "AEK Athens")
    assert res["scores"] == [{"name": "Como", "score": "2"}, {"name": "AEK Athens", "score": "0"}]


def test_bets_settle_from_goldedge_result(monkeypatch):
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from valuebet.db.models import Base, Bet, Signal
    from valuebet.db.repository import record_bet, settle_pending_bets
    from valuebet.sources import goldedge

    url = "https://www.goldedge.net/odds/handball/x/y/como-aek-athens-38czyxt"

    def fake_results(events, **_):
        assert events == {url: ("Como", "AEK Athens")}
        return {url: {"id": url, "home_team": "Como", "away_team": "AEK Athens", "completed": True,
                      "scores": [{"name": "Como", "score": "2"}, {"name": "AEK Athens", "score": "0"}]}}

    monkeypatch.setattr(goldedge, "fetch_goldedge_results", fake_results)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        sigs = []
        for sel in ("Como", "AEK Athens"):
            sig = Signal(event_id=5, event_name="Como vs AEK Athens", market_type="MATCH_ODDS",
                         selection=sel, sport="handball", fair_prob=0.5, target_odds=2.0, edge=0.05,
                         recommended_stake=5.0, source_url=url,
                         event_start_time=datetime.now(UTC) - timedelta(hours=4))
            session.add(sig)
            session.flush()
            record_bet(session, sig, placed_odds=2.0, stake=5.0, dry_run=True)
            sigs.append(sig)

        assert settle_pending_bets(session) == 2
        bets = {b.selection: b for b in session.query(Bet).all()}
        assert bets["Como"].outcome == "won" and bets["Como"].profit == 5.0
        assert bets["AEK Athens"].outcome == "lost" and bets["AEK Athens"].profit == -5.0
        assert bets["Como"].final_score == "2 - 0"
        assert "auto_settled_by:goldedge" in bets["Como"].note


# --- Betano's own price from the match page -------------------------------

def _list_row():
    return {
        "href": "/odds/football/europe/champions-league/alpha-beta-abc1234",
        "start": "2099-01-01T20:00:00Z",
        "live": False,
        "teams": ["Alpha", "Beta"],
        "cells": [
            {"title": "Bet365 · fair 2.00", "odds": "2.02", "book": "Bet365"},
            {"title": "BetRivers · fair 3.60", "odds": "3.70", "book": "BetRivers"},
            {"title": "Hard Rock Bet · fair 4.50", "odds": "4.95", "book": "Hard Rock Bet"},
        ],
    }


def _book_table(betano_age="4m", betano=("1.95", "3.50", "4.70"), pin=("1.94", "3.50", "4.37")):
    return [
        {"book": "Pinnacle", "age": "4m", "odds": list(pin)},
        {"book": "Bet365", "age": "4m", "odds": ["2.02", "3.40", "4.40"]},
        {"book": "Betano", "age": betano_age, "odds": list(betano)},
    ]


def test_best_price_edge_bounds_the_row():
    from valuebet.sources.goldedge import best_price_edge

    assert best_price_edge(_list_row()) == pytest.approx(4.95 / 4.50 - 1)
    assert best_price_edge({**_list_row(), "live": True}) == 0.0


def test_match_page_reveals_betano_value_hidden_behind_a_better_price():
    from datetime import UTC, datetime

    from valuebet.sources.goldedge import parse_event_row, row_with_book_prices

    now = datetime(2026, 1, 1, tzinfo=UTC)
    kw = {"now": now, "min_edge": 0.02, "stake_fn": lambda p, o: 5.0}
    # The list row names no Betano cell, so on its own it yields nothing.
    assert parse_event_row(_list_row(), **kw) == []

    repriced = row_with_book_prices(_list_row(), _book_table())
    sigs = parse_event_row(repriced, **kw)
    assert [s.selection for s in sigs] == ["Beta"]
    assert sigs[0].target_odds == 4.70
    assert sigs[0].edge == pytest.approx(4.70 / 4.50 - 1, abs=1e-4)
    assert sigs[0].all_market_odds == {"Alpha": 1.95, "Draw": 3.50, "Beta": 4.70}


def test_match_page_price_rejected_when_stale_missing_or_misaligned():
    from valuebet.sources.goldedge import row_with_book_prices

    assert row_with_book_prices(_list_row(), _book_table(betano_age="3h")) is None
    assert row_with_book_prices(_list_row(), _book_table(betano_age="")) is None
    assert row_with_book_prices(_list_row(), _book_table()[:2]) is None  # no Betano row
    assert row_with_book_prices(_list_row(), _book_table(betano=("1.95", "4.70"))) is None


def test_moved_pinnacle_line_raises_the_fair_price():
    from datetime import UTC, datetime

    from valuebet.sources.goldedge import parse_event_row, row_with_book_prices

    # Pinnacle now pays 4.80 on Beta: its de-vigged fair price is above Betano's 4.70.
    repriced = row_with_book_prices(_list_row(), _book_table(pin=("1.85", "3.45", "4.80")))
    sigs = parse_event_row(
        repriced, now=datetime(2026, 1, 1, tzinfo=UTC), min_edge=0.02, stake_fn=lambda p, o: 5.0
    )
    assert sigs == []

