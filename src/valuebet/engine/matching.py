"""Cross-source market & selection matching.

Different books name teams and structure events differently. For the pilot we use
a normalised-name match within the same sport + market type, plus strict event identity
verification so that different matches sharing a team (e.g. Vitória SC vs Porto vs
Vitória SC vs Gil Vicente) or generic markets (Over/Under Totals) NEVER cross-match.
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
_DRAW_LABELS = {"draw", "tie", "x"}


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


def _is_draw(key: str) -> bool:
    return key.strip().lower() in _DRAW_LABELS


def _non_draw_keys(keys: set[str]) -> set[str]:
    return {k for k in keys if not _is_draw(k)}


def _is_generic_selection_market(market_type: str) -> bool:
    mt = (market_type or "").upper()
    return mt.startswith("TOTALS") or mt.startswith("BTTS") or "OVER_UNDER" in mt


def _events_compatible(a: MarketSnapshot, b: MarketSnapshot) -> bool:
    """Ensure both snapshots belong to the exact same sporting fixture."""
    if a.event_id and b.event_id:
        if a.event_id == b.event_id:
            return True
        # If both are from the same feed provider (like The Odds API hash ids of same length),
        # different IDs mean they are definitively different matches.
        if len(a.event_id) >= 16 and len(a.event_id) == len(b.event_id):
            return False

    if a.event_name and b.event_name:
        if normalise(a.event_name) == normalise(b.event_name):
            return True
        # Support unordered "Home vs Away" check
        parts_a = {normalise(p) for p in re.split(r"\s+vs\.?\s+|\s+v\s+|\s*-\s*", a.event_name.lower()) if p.strip()}
        parts_b = {normalise(p) for p in re.split(r"\s+vs\.?\s+|\s+v\s+|\s*-\s*", b.event_name.lower()) if p.strip()}
        if len(parts_a) >= 2 and len(parts_b) >= 2 and parts_a == parts_b:
            return True

    return False


def match_markets(
    target: MarketSnapshot, candidates: list[MarketSnapshot]
) -> MarketSnapshot | None:
    """Find the candidate market whose selection set best overlaps `target`.

    Rules:
    - 1X2 / Match Winner: Both participating teams (all non-draw selections) MUST match.
      "Draw" + one common team (e.g. Vitória SC vs Porto vs Vitória SC vs Gil Vicente)
      can NEVER match across different fixtures.
    - Totals / Spreads / BTTS: Fixture compatibility (same event_id or event_name) is mandatory
      since generic labels ('Over 2.5') cannot identify the match by name alone.
    """
    target_keys = {selection_key(s) for s in target.selections()}
    target_non_draw = _non_draw_keys(target_keys)
    is_generic = _is_generic_selection_market(target.market_type)

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

        if is_generic:
            # Over/Under & BTTS: Selections ("Over 2.5") have no team names; fixture compatibility is mandatory
            if not same_event:
                continue
        else:
            # Match Winner / 1X2 / Moneyline:
            # If both have provider event IDs of identical length (The Odds API), different ID = different event
            if target.event_id and cand.event_id and len(target.event_id) >= 16 and len(target.event_id) == len(cand.event_id):
                if target.event_id != cand.event_id:
                    continue

            cand_keys = {selection_key(s) for s in cand.selections()}
            cand_non_draw = _non_draw_keys(cand_keys)

            # In team sports, BOTH participating teams must match.
            # A candidate missing one of the target teams can NEVER be the same match.
            if target_non_draw and cand_non_draw:
                if target_non_draw != cand_non_draw:
                    continue

        cand_keys = {selection_key(s) for s in cand.selections()}
        overlap = len(target_keys & cand_keys)
        if (overlap > best_overlap) or (overlap == best_overlap and same_event and not best_same_event):
            best_overlap = overlap
            best = cand
            best_same_event = same_event

    required = len(target_keys) if not is_generic else max(2, len(target_keys))
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
