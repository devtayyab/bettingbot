"""Betfair market-quality scoring and the edge uncertainty buffer.

Betfair is only a trustworthy probability benchmark when real money supports the
price being quoted *right now*. Historical volume (``total_matched``) and current
executable depth measure different things, so both are scored, together with the
relative back/lay spread:

    BACK 1.80 — €450 / LAY 1.81 — €620   -> excellent reference
    BACK 1.80 — €7   / LAY 1.90 — €11    -> poor reference, even at €20k matched

Pipeline:
    Betfair fair probability -> quality filter -> compare bookmaker price
    -> edge -> signal only if edge > base threshold + uncertainty + slippage.

Everything here is pure (no I/O) so it is fully unit-testable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..core import odds_math
from ..core.models import MarketSnapshot, Quote

# Relative weight of each component in the overall score. Current depth matters
# most: it is what actually supports the probability we use.
_W_VOLUME = 0.3
_W_DEPTH = 0.4
_W_SPREAD = 0.3


@dataclass(frozen=True)
class QualityThresholds:
    min_total_matched: float      # volume at which the volume score starts (0)
    strong_total_matched: float   # volume treated as fully strong (1)
    min_liquidity: float          # depth near price at which depth score starts (0)
    strong_liquidity: float       # depth near price treated as fully strong (1)
    good_spread: float            # relative spread at/below which spread score is 1
    max_spread: float             # relative spread at/above which spread score is 0
    depth_band: float             # relative distance from best price counted as "near"


@dataclass(frozen=True)
class SelectionQuality:
    selection: str
    spread: float | None            # relative back/lay spread, None without lay price
    back_depth: float | None        # € available to back within depth_band of best
    lay_depth: float | None         # € available to lay within depth_band of best
    half_spread_prob: float | None  # (1/back - 1/lay) / 2 — probability uncertainty


@dataclass(frozen=True)
class MarketQuality:
    score: float                  # 0..1, weighted over components that had data
    volume_score: float | None
    depth_score: float | None
    spread_score: float | None
    completeness: float           # share of the three components backed by data
    selections: tuple[SelectionQuality, ...]  # index-aligned with snapshot.quotes

    @property
    def effective_score(self) -> float:
        """Score discounted for missing data: unknown is never treated as quality."""
        return self.score * self.completeness


def _ramp(value: float, lo: float, hi: float) -> float:
    """0 at ``lo``, 1 at ``hi``, linear in between, clamped."""
    if hi <= lo:
        return 1.0 if value >= lo else 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _log_ramp(value: float, lo: float, hi: float) -> float:
    """Like ``_ramp`` but on a log scale — €10k→€25k matters as much as €1k→€2.5k."""
    if value <= 0:
        return 0.0
    lo = max(lo, 1.0)
    if hi <= lo:
        return 1.0 if value >= lo else 0.0
    return _ramp(math.log(value), math.log(lo), math.log(hi))


def ladder_depth(
    ladder: Sequence[tuple[float, float]] | None,
    best_price: float,
    band: float,
    fallback: float | None,
) -> float | None:
    """Total stake available within ``band`` (relative) of the best price.

    €20 at 1.80 means little on its own, but not if €1,500 sits at 1.79/1.78.
    Without ladder data we fall back to the best-level size.
    """
    if not ladder:
        return fallback
    if best_price <= 0:
        return fallback
    total = 0.0
    for price, size in ladder:
        if price > 0 and abs(price - best_price) / best_price <= band + 1e-9:
            total += size
    return total


def _assess_selection(q: Quote, band: float) -> SelectionQuality:
    if q.lay_odds is None or q.lay_odds <= 1.0 or q.decimal_odds <= 1.0:
        return SelectionQuality(q.selection, None, q.back_liquidity, q.lay_liquidity, None)
    spread = odds_math.relative_spread(q.decimal_odds, q.lay_odds)
    back_depth = ladder_depth(q.back_ladder, q.decimal_odds, band, q.back_liquidity)
    lay_depth = ladder_depth(q.lay_ladder, q.lay_odds, band, q.lay_liquidity)
    half_spread = abs(1.0 / q.decimal_odds - 1.0 / q.lay_odds) / 2.0
    return SelectionQuality(q.selection, spread, back_depth, lay_depth, half_spread)


def assess_market(snapshot: MarketSnapshot, t: QualityThresholds) -> MarketQuality:
    """Score how much the reference market's current prices can be trusted."""
    selections = tuple(_assess_selection(q, t.depth_band) for q in snapshot.quotes)

    volume_score: float | None = None
    if snapshot.total_matched is not None:
        volume_score = _log_ramp(snapshot.total_matched, t.min_total_matched, t.strong_total_matched)

    # The market is only as good as its weakest selection.
    depths = [
        min(d for d in (sq.back_depth, sq.lay_depth) if d is not None)
        for sq in selections
        if sq.back_depth is not None or sq.lay_depth is not None
    ]
    depth_score: float | None = (
        _ramp(min(depths), t.min_liquidity, t.strong_liquidity) if depths else None
    )

    spreads = [sq.spread for sq in selections if sq.spread is not None]
    spread_score: float | None = (
        1.0 - _ramp(max(spreads), t.good_spread, t.max_spread) if spreads else None
    )

    parts = [
        (w, v)
        for w, v in ((_W_VOLUME, volume_score), (_W_DEPTH, depth_score), (_W_SPREAD, spread_score))
        if v is not None
    ]
    weight = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / weight if weight > 0 else 0.0
    completeness = len(parts) / 3.0

    return MarketQuality(
        score=round(score, 4),
        volume_score=volume_score,
        depth_score=depth_score,
        spread_score=spread_score,
        completeness=completeness,
        selections=selections,
    )


def edge_buffer(
    *,
    offered_odds: float,
    half_spread_prob: float | None,
    quality: MarketQuality,
    slippage: float,
    quality_penalty_max: float,
    unknown_spread_prob: float,
) -> float:
    """Extra edge required on top of the base threshold before signalling.

    - Uncertainty: edge = p * odds - 1, so a probability uncertainty ``dp`` moves
      the edge by ``odds * dp``. ``dp`` is half the reference back/lay spread in
      probability terms (or ``unknown_spread_prob`` when no lay price exists).
    - Quality: up to ``quality_penalty_max`` more for thin / unknown markets.
    - Slippage: target-book price movement and execution cost.

    Example: bookmaker 2.05 vs Betfair fair 1.90 is a ~7.9% raw edge, but on a
    thin Betfair book the buffer can eat most of it — that is intentional.
    """
    dp = half_spread_prob if half_spread_prob is not None else unknown_spread_prob
    uncertainty = offered_odds * dp
    quality_term = (1.0 - quality.effective_score) * quality_penalty_max
    return uncertainty + quality_term + slippage
