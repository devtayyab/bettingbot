"""Cross-source market & selection matching.

Different books name teams and structure events differently. For the pilot we use
a normalised-name match within the same sport + market type. This is deliberately
conservative: if we cannot confidently align selections across sources, we skip
the market rather than risk acting on a mismatch.
"""

from __future__ import annotations

import re

from ..core.models import MarketSnapshot

_NORMALISE_RE = re.compile(r"[^a-z0-9]+")
# Common aliases / noise tokens stripped before comparison.
_NOISE = {"fc", "cf", "sc", "afc", "the"}


def normalise(name: str) -> str:
    tokens = _NORMALISE_RE.sub(" ", name.lower()).split()
    tokens = [t for t in tokens if t not in _NOISE]
    return " ".join(sorted(tokens))


def selection_key(name: str) -> str:
    return normalise(name)


def match_markets(
    target: MarketSnapshot, candidates: list[MarketSnapshot]
) -> MarketSnapshot | None:
    """Find the candidate market whose selection set best overlaps `target`."""
    target_keys = {selection_key(s) for s in target.selections()}
    best: MarketSnapshot | None = None
    best_overlap = 0
    for cand in candidates:
        if cand.market_type != target.market_type or cand.sport != target.sport:
            continue
        cand_keys = {selection_key(s) for s in cand.selections()}
        overlap = len(target_keys & cand_keys)
        if overlap > best_overlap:
            best_overlap = overlap
            best = cand
    # Require a strict majority of selections to align.
    if best is not None and best_overlap >= max(2, len(target_keys) - 1):
        return best
    return None
