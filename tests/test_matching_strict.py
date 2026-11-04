"""Strict matching tests verifying that different matches sharing a team (e.g. Vitória SC vs Porto
vs Vitória SC vs Gil Vicente) NEVER match, and raw Betfair odds are preserved.
"""

from datetime import UTC, datetime, timedelta

import pytest

from valuebet.core.models import MarketSnapshot, MarketStatus, Quote, Sport
from valuebet.engine.matching import match_markets


def _mkt(
    event_id: str,
    event_name: str,
    selections: list[str],
    market_type: str = "MATCH_ODDS",
) -> MarketSnapshot:
    now = datetime.now(UTC)
    quotes = [
        Quote(source="test", selection=s, decimal_odds=2.0 + i * 0.5, captured_at=now)
        for i, s in enumerate(selections)
    ]
    return MarketSnapshot(
        event_id=event_id,
        event_name=event_name,
        market_id=f"mkt-{event_id}",
        market_type=market_type,
        sport=Sport.SOCCER,
        status=MarketStatus.PREMATCH,
        start_time=now + timedelta(hours=2),
        quotes=quotes,
    )


def test_match_odds_rejects_different_fixture_sharing_one_team_and_draw():
    # Vitória SC vs FC Porto (Stoiximan target)
    target = _mkt(
        event_id="target-100",
        event_name="Vitória SC vs FC Porto",
        selections=["Vitória SC", "Draw", "FC Porto"],
    )

    # Vitória SC vs Gil Vicente (Betfair candidate from a different match)
    # Both have "Vitória SC" and "Draw", but "FC Porto" != "Gil Vicente"
    candidate_diff_match = _mkt(
        event_id="cand-200",
        event_name="Vitória SC vs Gil Vicente",
        selections=["Vitória SC", "Draw", "Gil Vicente"],
    )

    # Candidate with the TRUE matching fixture
    candidate_correct_match = _mkt(
        event_id="cand-300",
        event_name="Vitória SC vs FC Porto",
        selections=["Vitória SC", "Draw", "FC Porto"],
    )

    # When only the wrong fixture is in candidates, it MUST return None (NO MATCH)!
    assert match_markets(target, [candidate_diff_match]) is None

    # When the correct fixture is present, it matches the correct fixture
    matched = match_markets(target, [candidate_diff_match, candidate_correct_match])
    assert matched is not None
    assert matched.event_id == "cand-300"
    assert matched.event_name == "Vitória SC vs FC Porto"


def test_match_odds_accepts_reversed_home_away_order():
    target = _mkt(
        event_id="target-101",
        event_name="Arsenal vs Chelsea",
        selections=["Arsenal", "Draw", "Chelsea"],
    )
    candidate_reversed = _mkt(
        event_id="cand-101",
        event_name="Chelsea vs Arsenal",
        selections=["Chelsea", "Draw", "Arsenal"],
    )
    matched = match_markets(target, [candidate_reversed])
    assert matched is not None
    assert matched.event_id == "cand-101"


def test_provider_event_id_mismatch_rejects():
    # Two snapshots from The Odds API with different 32-char hash event_ids
    target = _mkt(
        event_id="468711100306238134abcdef12345678",
        event_name="Vitória SC vs FC Porto",
        selections=["Vitória SC", "Draw", "FC Porto"],
    )
    cand_wrong_id = _mkt(
        event_id="970480645591145485abcdef12345678",
        event_name="Vitória SC vs FC Porto",
        selections=["Vitória SC", "Draw", "FC Porto"],
    )
    # Different provider event_id means different fixture
    assert match_markets(target, [cand_wrong_id]) is None
