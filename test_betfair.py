import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

import logging
from valuebet.core.models import Sport
from valuebet.sources.betfair import BetfairSource

logging.basicConfig(level=logging.INFO)

def main():
    print("Initializing Betfair Playwright Scraper...")
    # headless=True so it runs in the background without UI
    scraper = BetfairSource(headless=True)
    
    print("\nFetching Live Football Markets from Betfair...")
    snapshots = scraper.fetch_markets(Sport.SOCCER, live=True)
    
    print(f"\nExtracted {len(snapshots)} markets.")
    for s in snapshots:
        print(f"\nEvent: {s.event_id} | Rule: {s.settlement_rule} | Suspended: {s.is_suspended}")
        print(f"Quotes:")
        for q in s.quotes:
            print(f"  - {q.selection}: {q.decimal_odds}")

if __name__ == "__main__":
    main()
