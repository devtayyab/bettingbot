"""Cross-source market & selection matching.

Different books name teams and structure events differently. For the pilot we use
a normalised-name match within the same sport + market type. This is deliberately
conservative: if we cannot confidently align selections across sources, we skip
the market rather than risk acting on a mismatch.
"""

from __future__ import annotations

import re

from ..core.models import MarketSnapshot, SettlementRule
from ..logging import get_logger

log = get_logger("engine.matching")

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
            
        # Feature 5: Settlement Rule matching
        if (cand.settlement_rule != SettlementRule.UNKNOWN and 
            target.settlement_rule != SettlementRule.UNKNOWN and 
            cand.settlement_rule != target.settlement_rule):
            log.debug("settlement_rule_mismatch", target=target.settlement_rule, cand=cand.settlement_rule)
            continue
            
        cand_keys = {selection_key(s) for s in cand.selections()}
        overlap = len(target_keys & cand_keys)
        if overlap > best_overlap:
            best_overlap = overlap
            best = cand
    # Require a strict majority of selections to align.
    required = max(2, len(target_keys) - 1)
    if best is not None and best_overlap >= required:
        return best

    # A silent None here is why a scan can report "0 signals" with no explanation:
    # if the feeds spell the teams differently, every market is dropped before any
    # value check runs. Full detail at debug; the engine reports a bounded sample
    # of these at info in its scan funnel, so the common case stays readable.
    log.debug(
        "market_unmatched",
        event_id=target.event_id,
        market_type=target.market_type,
        sport=getattr(target.sport, "value", str(target.sport)),
        target_selections=sorted(target_keys),
        best_overlap=best_overlap,
        required_overlap=required,
        best_candidate=sorted(
            {selection_key(s) for s in best.selections()}
        ) if best is not None else None,
        candidates_considered=len(candidates),
        # Without the other side's names there is nothing to compare against, so
        # a name-normalisation mismatch stays invisible. Capped to keep the line
        # readable when a feed returns hundreds of markets.
        candidate_selections=[
            sorted({selection_key(s) for s in c.selections()})
            for c in candidates
            if c.market_type == target.market_type and c.sport == target.sport
        ][:5],
    )
    return None
