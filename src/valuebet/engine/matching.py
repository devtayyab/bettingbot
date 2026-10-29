"""Cross-source market & selection matching.

Different books name teams and structure events differently. For the pilot we use
a normalised-name match within the same sport + market type, plus event identity
verification for generic markets (such as Over/Under Totals) where selection names
alone ('Over 2.5' / 'Under 2.5') do not identify the match.
"""

from __future__ import annotations

import re

from ..core.models import MarketSnapshot, SettlementRule
from ..logging import get_logger

log = get_logger("engine.matching")

# Preserve explicit handicap signs like (-1.5) or +2.5 while keeping hyphenated team
# names ("Paris Saint-Germain") split cleanly into words.
_SIGNED_NUM_RE = re.compile(r"(?:^|(?<=[\s(]))([+-])(\d+(?:\.\d+)?)")
_NORMALISE_RE = re.compile(r"[^a-z0-9]+")
_NOISE = {"fc", "cf", "sc", "afc", "the"}


def _encode_signed_numbers(text: str) -> str:
    def _repl(m: re.Match[str]) -> str:
        sign_word = "plus" if m.group(1) == "+" else "minus"
        return f" {sign_word} {m.group(2)} "

    return _SIGNED_NUM_RE.sub(_repl, text)


def normalise(name: str) -> str:
    prepared = _encode_signed_numbers(name.lower())
    tokens = _NORMALISE_RE.sub(" ", prepared).split()
    tokens = [t for t in tokens if t not in _NOISE]
    return " ".join(sorted(tokens))


def selection_key(name: str) -> str:
    return normalise(name)


def _is_generic_selection_market(market_type: str) -> bool:
    mt = (market_type or "").upper()
    return mt.startswith("TOTALS") or mt.startswith("BTTS") or "OVER_UNDER" in mt


def _events_compatible(a: MarketSnapshot, b: MarketSnapshot) -> bool:
    """For markets with generic selection labels (e.g. Over 2.5 / Under 2.5),
    ensure both snapshots belong to the exact same fixture."""
    if a.event_id and b.event_id and a.event_id == b.event_id:
        return True
    if a.event_name and b.event_name and normalise(a.event_name) == normalise(b.event_name):
        return True
    return False


def match_markets(
    target: MarketSnapshot, candidates: list[MarketSnapshot]
) -> MarketSnapshot | None:
    """Find the candidate market whose selection set best overlaps `target`."""
    target_keys = {selection_key(s) for s in target.selections()}
    needs_event_check = _is_generic_selection_market(target.market_type)

    best: MarketSnapshot | None = None
    best_overlap = 0
    best_same_event = False

    for cand in candidates:
        if cand.market_type != target.market_type or cand.sport != target.sport:
            continue

        if (
            cand.settlement_rule != SettlementRule.UNKNOWN
            and target.settlement_rule != SettlementRule.UNKNOWN
            and cand.settlement_rule != target.settlement_rule
        ):
            log.debug("settlement_rule_mismatch", target=target.settlement_rule, cand=cand.settlement_rule)
            continue

        same_event = _events_compatible(target, cand)
        if needs_event_check and not same_event:
            continue

        cand_keys = {selection_key(s) for s in cand.selections()}
        overlap = len(target_keys & cand_keys)
        if (overlap > best_overlap) or (overlap == best_overlap and same_event and not best_same_event):
            best_overlap = overlap
            best = cand
            best_same_event = same_event

    required = max(2, len(target_keys) - 1)
    if best is not None and best_overlap >= required:
        return best

    log.debug(
        "market_unmatched",
        event_id=target.event_id,
        event_name=target.event_name,
        market_type=target.market_type,
        sport=getattr(target.sport, "value", str(target.sport)),
        target_selections=sorted(target_keys),
        best_overlap=best_overlap,
        required_overlap=required,
        best_candidate=sorted({selection_key(s) for s in best.selections()}) if best is not None else None,
        candidates_considered=len(candidates),
        candidate_selections=[
            sorted({selection_key(s) for s in c.selections()})
            for c in candidates
            if c.market_type == target.market_type and c.sport == target.sport
        ][:5],
    )
    return None
