"""Tests for the Betfair Market Quality Score and the edge uncertainty buffer."""

from datetime import UTC, datetime

from valuebet.core.models import MarketSnapshot, MarketStatus, Quote, Sport
from valuebet.engine.market_quality import (
    QualityThresholds,
    assess_market,
    edge_buffer,
    ladder_depth,
)

NOW = datetime.now(UTC)
T = QualityThresholds(
    min_total_matched=10_000,
    strong_total_matched=25_000,
    min_liquidity=100,
    strong_liquidity=500,
    good_spread=0.01,
    max_spread=0.02,
    depth_band=0.02,
)


def _snap(quotes, total_matched=30_000.0):
    return MarketSnapshot(
        event_id="e", market_id="m", market_type="MATCH_ODDS", sport=Sport.SOCCER,
        status=MarketStatus.PREMATCH, start_time=NOW, total_matched=total_matched, quotes=quotes,
    )


def test_ladder_depth_sums_levels_near_best_price():
    ladder = ((1.80, 20.0), (1.79, 800.0), (1.78, 700.0), (1.60, 5000.0))
    # 1.60 is ~11% away, outside a 2% band.
    assert ladder_depth(ladder, 1.80, 0.02, fallback=20.0) == 1520.0


def test_ladder_depth_falls_back_without_ladder():
    assert ladder_depth(None, 1.80, 0.02, fallback=42.0) == 42.0


def test_tight_deep_market_scores_high():
    q = Quote("betfair", "A", 1.80, NOW, lay_odds=1.81, back_liquidity=450, lay_liquidity=620)
    mq = assess_market(_snap([q]), T)
    assert mq.spread_score == 1.0
    assert mq.volume_score == 1.0
    assert mq.score > 0.9
    assert mq.completeness == 1.0


def test_thin_wide_market_scores_low_even_with_volume():
    q = Quote("betfair", "A", 1.80, NOW, lay_odds=1.90, back_liquidity=7, lay_liquidity=11)
    mq = assess_market(_snap([q], total_matched=20_000), T)
    assert mq.spread_score == 0.0
    assert mq.depth_score == 0.0
    assert mq.score < 0.35


def test_depth_behind_best_price_rescues_depth_score():
    q = Quote(
        "betfair", "A", 1.80, NOW, lay_odds=1.81, back_liquidity=20, lay_liquidity=20,
        back_ladder=((1.80, 20.0), (1.79, 800.0)), lay_ladder=((1.81, 20.0), (1.82, 900.0)),
    )
    mq = assess_market(_snap([q]), T)
    assert mq.depth_score == 1.0


def test_unknown_data_has_zero_effective_score():
    q = Quote("betfair", "A", 1.80, NOW)  # Odds-API-style: no lay, no depth, no volume
    mq = assess_market(_snap([q], total_matched=None), T)
    assert mq.completeness == 0.0
    assert mq.effective_score == 0.0


def test_edge_buffer_components():
    q = Quote("betfair", "A", 1.80, NOW, lay_odds=1.82, back_liquidity=1000, lay_liquidity=1000)
    mq = assess_market(_snap([q]), T)
    sq = mq.selections[0]
    # half spread in prob: (1/1.80 - 1/1.82) / 2 ≈ 0.00305
    assert sq.half_spread_prob is not None
    assert abs(sq.half_spread_prob - 0.003053) < 1e-5
    buf = edge_buffer(
        offered_odds=2.0, half_spread_prob=sq.half_spread_prob, quality=mq,
        slippage=0.005, quality_penalty_max=0.02, unknown_spread_prob=0.0025,
    )
    expected = 2.0 * sq.half_spread_prob + (1 - mq.effective_score) * 0.02 + 0.005
    assert abs(buf - expected) < 1e-12


def test_thin_market_requires_more_edge_than_deep_market():
    deep = assess_market(_snap([Quote("betfair", "A", 1.80, NOW, lay_odds=1.81,
                                      back_liquidity=1000, lay_liquidity=1000)]), T)
    thin = assess_market(_snap([Quote("betfair", "A", 1.80, NOW, lay_odds=1.83,
                                      back_liquidity=110, lay_liquidity=110)], 11_000), T)
    kw = dict(offered_odds=2.05, slippage=0.005, quality_penalty_max=0.02, unknown_spread_prob=0.0025)
    b_deep = edge_buffer(half_spread_prob=deep.selections[0].half_spread_prob, quality=deep, **kw)
    b_thin = edge_buffer(half_spread_prob=thin.selections[0].half_spread_prob, quality=thin, **kw)
    assert b_thin > b_deep
