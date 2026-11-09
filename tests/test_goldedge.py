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

