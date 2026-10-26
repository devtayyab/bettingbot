"""Tests for result resolving and auto-settlement of bets."""

from valuebet.core.results import BetOutcome, TheOddsAPIResultResolver


def test_determine_match_outcome_home_win():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "3"},
        {"name": "Chelsea", "score": "1"},
    ]
    # Bet on Arsenal (Home)
    outcome_home = resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", scores)
    assert outcome_home == BetOutcome.WON

    # Bet on Chelsea (Away)
    outcome_away = resolver.determine_match_outcome("Chelsea", "Arsenal", "Chelsea", scores)
    assert outcome_away == BetOutcome.LOST

    # Bet on Draw
    outcome_draw = resolver.determine_match_outcome("Draw", "Arsenal", "Chelsea", scores)
    assert outcome_draw == BetOutcome.LOST


def test_determine_match_outcome_away_win():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "0"},
        {"name": "Chelsea", "score": "2"},
    ]
    outcome_home = resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", scores)
    assert outcome_home == BetOutcome.LOST

    outcome_away = resolver.determine_match_outcome("Chelsea", "Arsenal", "Chelsea", scores)
    assert outcome_away == BetOutcome.WON


def test_determine_match_outcome_draw():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    scores = [
        {"name": "Arsenal", "score": "2"},
        {"name": "Chelsea", "score": "2"},
    ]
    outcome_draw = resolver.determine_match_outcome("Draw", "Arsenal", "Chelsea", scores)
    assert outcome_draw == BetOutcome.WON

    outcome_home = resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", scores)
    assert outcome_home == BetOutcome.LOST


def test_determine_match_outcome_pending_when_scores_missing():
    resolver = TheOddsAPIResultResolver(api_key="dummy")
    assert resolver.determine_match_outcome("Arsenal", "Arsenal", "Chelsea", []) == BetOutcome.PENDING
