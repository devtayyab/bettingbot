"""Pure odds mathematics: implied probability, margin removal (de-vig), edge, Kelly.

Everything here is a pure function of its inputs — no I/O, no globals — so the
engine's decisions are fully reproducible and unit-testable. This is the part of
the system that decides whether an "edge" is real, so it is the most important.
"""

from __future__ import annotations

from enum import Enum


class DevigMethod(str, Enum):
    """How to strip the bookmaker margin (overround) from a set of odds.

    MULTIPLICATIVE (a.k.a. proportional / Vig-Free): divide each implied prob by
        the booksum. Simple, market-standard, slightly biased toward favorites.
    ADDITIVE: subtract an equal share of the margin from each implied prob.
        Biases toward longshots; rarely the right default.
    SHIN: estimates an insider-trading parameter z; best-known approximation of a
        sharp book's true probabilities. Used here for the Pinnacle confirmation.
    """

    MULTIPLICATIVE = "multiplicative"
    ADDITIVE = "additive"
    SHIN = "shin"


def implied_prob(decimal_odds: float) -> float:
    """Raw implied probability of a single decimal price (includes margin)."""
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def booksum(decimal_odds: list[float]) -> float:
    """Sum of implied probabilities across a market = 1 + overround."""
    return sum(implied_prob(o) for o in decimal_odds)


def overround(decimal_odds: list[float]) -> float:
    """Bookmaker margin as a fraction (e.g. 0.05 = 5% margin)."""
    return booksum(decimal_odds) - 1.0


def devig(decimal_odds: list[float], method: DevigMethod = DevigMethod.MULTIPLICATIVE) -> list[float]:
    """Return fair (margin-free) probabilities for every selection in a market.

    The returned list is index-aligned with the input and always sums to 1.0.
    """
    if not decimal_odds:
        return []
    raw = [implied_prob(o) for o in decimal_odds]
    total = sum(raw)

    if method is DevigMethod.MULTIPLICATIVE:
        return [p / total for p in raw]

    if method is DevigMethod.ADDITIVE:
        n = len(raw)
        margin = total - 1.0
        fair = [p - margin / n for p in raw]
        # Renormalise to guard against tiny negatives on lopsided books.
        fair = [max(p, 1e-9) for p in fair]
        s = sum(fair)
        return [p / s for p in fair]

    if method is DevigMethod.SHIN:
        return _shin_devig(raw)

    raise ValueError(f"unknown devig method: {method}")


def _shin_devig(raw_probs: list[float], iterations: int = 100) -> list[float]:
    """Shin (1992) margin removal. Solves for z, the prob of betting against insiders.

    Fair prob_i = (sqrt(z^2 + 4(1-z) * raw_i^2 / booksum) - z) / (2(1-z))
    z is found by fixed-point iteration so the fair probs sum to 1.
    """
    bsum = sum(raw_probs)
    z = 0.0
    for _ in range(iterations):
        denom = 2.0 * (1.0 - z)
        fair = [
            (((z * z + 4.0 * (1.0 - z) * (p * p) / bsum) ** 0.5) - z) / denom
            for p in raw_probs
        ]
        s = sum(fair)
        # Update z toward the value that makes the probs normalise.
        new_z = max(0.0, min(0.5, z + (s - 1.0)))
        if abs(new_z - z) < 1e-12:
            z = new_z
            break
        z = new_z
    denom = 2.0 * (1.0 - z)
    fair = [
        (((z * z + 4.0 * (1.0 - z) * (p * p) / bsum) ** 0.5) - z) / denom
        for p in raw_probs
    ]
    s = sum(fair)
    return [p / s for p in fair]


def fair_odds(fair_prob: float) -> float:
    """Convert a fair probability back to fair decimal odds."""
    if not 0.0 < fair_prob < 1.0:
        raise ValueError(f"fair prob must be in (0,1), got {fair_prob}")
    return 1.0 / fair_prob


def edge(fair_prob: float, offered_odds: float) -> float:
    """Expected ROI per unit stake when true prob is `fair_prob` and you get `offered_odds`.

    edge = fair_prob * offered_odds - 1
      > 0  => positive expected value
      = 0  => fair / break-even
    e.g. fair_prob 0.55, odds 2.0 -> 0.55*2 - 1 = 0.10 (a 10% edge).
    """
    return fair_prob * offered_odds - 1.0


def kelly_fraction(fair_prob: float, offered_odds: float) -> float:
    """Full-Kelly fraction of bankroll to stake. Returns 0.0 when there is no edge.

    f* = (b*p - q) / b , where b = offered_odds - 1, q = 1 - p
       = (p*offered_odds - 1) / (offered_odds - 1)
       = edge / (offered_odds - 1)
    """
    e = edge(fair_prob, offered_odds)
    if e <= 0.0:
        return 0.0
    b = offered_odds - 1.0
    if b <= 0.0:
        return 0.0
    return e / b


def kelly_stake(
    fair_prob: float,
    offered_odds: float,
    bankroll: float,
    fraction: float = 1.0,
    max_stake: float | None = None,
) -> float:
    """Recommended stake = bankroll * fractional-Kelly, capped at max_stake.

    `fraction` is the Kelly multiplier (e.g. 0.25 = quarter-Kelly) to dampen variance.
    """
    f = kelly_fraction(fair_prob, offered_odds) * fraction
    stake = bankroll * f
    if max_stake is not None:
        stake = min(stake, max_stake)
    return round(max(stake, 0.0), 2)
