"""Debug script: show exactly why signals are or aren't generated."""
import sys
sys.path.insert(0, "/app/src")

from valuebet.config import get_settings
from valuebet.core.models import Sport
from valuebet.sources.the_odds_api import TheOddsAPISource

s = get_settings()
print("=== Settings ===")
print(f"  edge_threshold        : {s.edge_threshold}")
print(f"  confirmation_tolerance: {s.confirmation_tolerance}")
print(f"  require_confirmation  : {s.require_confirmation}")
print(f"  favorite_min_prob     : {s.favorite_min_prob}")

print("\n=== Fetching Betfair odds ===")
bf = TheOddsAPISource(target_bookmaker="betfair_ex_uk", name="betfair")
bf_snaps = bf.fetch_markets(Sport.SOCCER)
print(f"  Betfair events: {len(bf_snaps)}")

print("\n=== Fetching Pinnacle odds ===")
pin = TheOddsAPISource(target_bookmaker="pinnacle", name="pinnacle")
pin_snaps = pin.fetch_markets(Sport.SOCCER)
print(f"  Pinnacle events: {len(pin_snaps)}")

print("\n=== Fetching Betano/Stoiximan odds ===")
tgt = TheOddsAPISource(target_bookmaker="betano_uk", name="stoiximan")
tgt_snaps = tgt.fetch_markets(Sport.SOCCER)
print(f"  Betano events: {len(tgt_snaps)}")

# Build lookups
pin_by_event = {snap.event_id: snap for snap in pin_snaps}
tgt_by_event = {snap.event_id: snap for snap in tgt_snaps}

print("\n=== Per-Selection Edge Analysis ===")
found_value = 0
rejected_edge = 0
rejected_confirmation = 0
rejected_low_prob = 0
rejected_no_tgt = 0

for snap in bf_snaps:
    if snap.event_id not in tgt_by_event:
        rejected_no_tgt += 1
        continue

    tgt_snap = tgt_by_event[snap.event_id]
    pin_snap = pin_by_event.get(snap.event_id)

    for quote in snap.quotes:
        # Betfair exchange price -> fair probability
        fair_prob = 1.0 / quote.decimal_odds

        if fair_prob < s.favorite_min_prob:
            rejected_low_prob += 1
            continue

        # Match selection in target bookmaker
        tgt_quote = next(
            (q for q in tgt_snap.quotes if q.selection == quote.selection), None
        )
        if not tgt_quote:
            continue

        edge = fair_prob - (1.0 / tgt_quote.decimal_odds)

        if edge >= s.edge_threshold:
            # Check Pinnacle confirmation
            conf_note = ""
            if pin_snap:
                pin_quote = next(
                    (q for q in pin_snap.quotes if q.selection == quote.selection), None
                )
                if pin_quote and s.require_confirmation:
                    confirm_prob = 1.0 / pin_quote.decimal_odds
                    diff = abs(confirm_prob - fair_prob)
                    if diff > s.confirmation_tolerance:
                        rejected_confirmation += 1
                        conf_note = f" ✗ CONFIRM FAIL (diff={diff:.3f}>{s.confirmation_tolerance})"
                    else:
                        found_value += 1
                        conf_note = f" ✓ SIGNAL"
                else:
                    found_value += 1
                    conf_note = " ✓ SIGNAL (confirm not required)"
            else:
                found_value += 1
                conf_note = " ✓ SIGNAL (no pinnacle data)"

            event_name = getattr(snap, 'event_name', snap.event_id)
            print(f"  {str(event_name)[:35]:35} {quote.selection:12} "
                  f"edge={edge:+.3f} betfair={quote.decimal_odds:.2f} "
                  f"betano={tgt_quote.decimal_odds:.2f}{conf_note}")
        else:
            rejected_edge += 1

print(f"\n=== Summary ===")
print(f"  ✓ Signals found      : {found_value}")
print(f"  ✗ Edge too low (<{s.edge_threshold:.2f}) : {rejected_edge}")
print(f"  ✗ Confirm failed     : {rejected_confirmation}")
print(f"  ✗ Prob too low       : {rejected_low_prob}")
print(f"  ✗ No Betano coverage : {rejected_no_tgt}")
print(f"\n  Total Betfair events : {len(bf_snaps)}")
print(f"  Total Betano events  : {len(tgt_snaps)}")
