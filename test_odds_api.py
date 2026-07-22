import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

import logging
from valuebet.core.models import Sport
from valuebet.sources.the_odds_api import TheOddsAPISource

logging.basicConfig(level=logging.INFO)

def main():
    print("Testing The Odds API Integration...")
    
    # 1. Test Betfair Extraction via The Odds API
    bf_source = TheOddsAPISource(target_bookmaker="betfair_ex_uk", name="betfair")
    snapshots = bf_source.fetch_markets(Sport.SOCCER, live=False)
    
    print(f"\nExtracted {len(snapshots)} Betfair market snapshots via The Odds API.")
    for s in snapshots[:5]:
        print(f"\nEvent: {s.event_id} | Sport: {s.sport.value} | Settlement: {s.settlement_rule}")
        print("Quotes:")
        for q in s.quotes:
            print(f"  - {q.selection}: {q.decimal_odds} (Source: {q.source})")

    # 2. Test Pinnacle Extraction via The Odds API
    pin_source = TheOddsAPISource(target_bookmaker="pinnacle", name="pinnacle")
    pin_snapshots = pin_source.fetch_markets(Sport.SOCCER, live=False)
    print(f"\nExtracted {len(pin_snapshots)} Pinnacle market snapshots via The Odds API.")

if __name__ == "__main__":
    main()
