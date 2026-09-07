"""Test script for Stoiximan placer on AWS or local."""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from valuebet.placement.base import PlacementRequest
from valuebet.placement.stoiximan import StoiximanPlacer


def main():
    print("========================================")
    print("Testing StoiximanPlacer on Server...")
    print("========================================")

    # On AWS EC2 (headless server), headless must be True
    placer = StoiximanPlacer(headless=True)
    # Always keep dry-run True for safety during testing
    placer.settings.placement_dry_run = True

    request = PlacementRequest(
        event_id="test_event_1",
        market_type="h2h",
        selection="Celtic",
        target_odds=1.27,
        stake=1.0,
        min_odds=1.05
    )

    try:
        print("Executing test placement in Dry-Run mode...")
        result = placer.place(request)
        print("\n---------------- RESULT ----------------")
        print(f"Status: {result.status.value}")
        print(f"Success: {result.success}")
        print(f"On platform (verified): {result.verified_on_platform}")
        print(f"Real bet: {result.is_real_bet}")
        print(f"Placed Odds: {result.placed_odds}")
        print(f"Accepted Stake: {result.accepted_stake}")
        print(f"Message: {result.message}")
        print(f"Dry Run: {result.dry_run}")
        print("----------------------------------------\n")
    except Exception as e:
        print(f"Exception during test: {e}")
    finally:
        placer.close()
        print("Placer session closed.")

if __name__ == "__main__":
    main()
