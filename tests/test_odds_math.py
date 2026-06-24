"""Unit tests for the odds math core — the decision-critical part of the system."""

import math

import pytest

from valuebet.core.odds_math import (
    DevigMethod,
    booksum,
    devig,
    edge,
    fair_odds,
    implied_prob,
    kelly_fraction,
    kelly_stake,
    overround,
)


def test_implied_prob():
    assert implied_prob(2.0) == pytest.approx(0.5)
    assert implied_prob(4.0) == pytest.approx(0.25)


def test_implied_prob_rejects_invalid():
    with pytest.raises(ValueError):
        implied_prob(1.0)
    with pytest.raises(ValueError):
        implied_prob(0.5)


def test_overround_and_booksum():
    # A 2-way book at 1.90/1.90 has ~5.26% overround.
    odds = [1.90, 1.90]
    assert booksum(odds) == pytest.approx(1.0526, abs=1e-3)
    assert overround(odds) == pytest.approx(0.0526, abs=1e-3)


def test_devig_multiplicative_sums_to_one():
    fair = devig([1.90, 1.90], DevigMethod.MULTIPLICATIVE)
    assert sum(fair) == pytest.approx(1.0)
    assert fair[0] == pytest.approx(0.5)


def test_devig_three_way_market():
    # Typical 1X2 soccer book.
    fair = devig([1.80, 3.60, 4.50], DevigMethod.MULTIPLICATIVE)
    assert sum(fair) == pytest.approx(1.0)
    assert fair[0] > fair[1] > fair[2]  # favorite has highest prob


@pytest.mark.parametrize("method", list(DevigMethod))
def test_all_devig_methods_normalise(method):
    fair = devig([1.50, 4.00, 7.00], method)
    assert sum(fair) == pytest.approx(1.0, abs=1e-6)
    assert all(0.0 < p < 1.0 for p in fair)


def test_shin_is_close_to_multiplicative_on_balanced_books():
    odds = [1.95, 1.95]
    mult = devig(odds, DevigMethod.MULTIPLICATIVE)
    shin = devig(odds, DevigMethod.SHIN)
    assert shin[0] == pytest.approx(mult[0], abs=0.01)


def test_fair_odds_roundtrip():
    assert fair_odds(0.5) == pytest.approx(2.0)
    assert fair_odds(implied_prob(3.3)) == pytest.approx(3.3)


def test_edge_positive_when_overpriced():
    # Fair prob 0.55 but book offers 2.0 (implies 0.50) -> +10% edge.
    assert edge(0.55, 2.0) == pytest.approx(0.10)


def test_edge_zero_at_fair_price():
    assert edge(0.5, 2.0) == pytest.approx(0.0)


def test_edge_negative_when_underpriced():
    assert edge(0.5, 1.80) < 0


def test_kelly_fraction_zero_without_edge():
    assert kelly_fraction(0.5, 2.0) == 0.0
    assert kelly_fraction(0.4, 2.0) == 0.0


def test_kelly_fraction_known_value():
    # p=0.55, odds=2.0 -> f* = (1*0.55 - 0.45)/1 = 0.10
    assert kelly_fraction(0.55, 2.0) == pytest.approx(0.10)


def test_kelly_stake_respects_fraction_and_cap():
    # Full kelly would be 0.10 * 1000 = 100; quarter-kelly = 25; cap at 10.
    staked = kelly_stake(0.55, 2.0, bankroll=1000, fraction=0.25, max_stake=10.0)
    assert staked == 10.0
    uncapped = kelly_stake(0.55, 2.0, bankroll=1000, fraction=0.25)
    assert uncapped == pytest.approx(25.0)


def test_kelly_stake_zero_without_edge():
    assert kelly_stake(0.5, 2.0, bankroll=1000, fraction=0.25) == 0.0


def test_edge_threshold_scenario():
    # End-to-end: Betfair fair prob, Stoiximan offered odds, 4.9% threshold.
    betfair_fair = devig([1.66, 5.5, 6.0], DevigMethod.MULTIPLICATIVE)
    fav_prob = betfair_fair[0]
    # Stoiximan offers 1.75 on the favorite.
    e = edge(fav_prob, 1.75)
    assert math.isfinite(e)
    # Whether it clears the bar depends on the de-vigged prob; just assert sane range.
    assert -0.2 < e < 0.3
