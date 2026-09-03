"""Standalone helper script to log in to Stoiximan and save session cookies to JSON.

Run:
    python scripts/save_stoiximan_cookies.py
or
    python -m valuebet.cli login-stoiximan
"""

import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "src"))

from valuebet.placement.stoiximan import interactive_login
from valuebet.placement.session_store import get_cookie_status


def main():
    print("=" * 60)
    print("Stoiximan Cookie Saver Helper")
    print("=" * 60)
    
    status = get_cookie_status()
    print(f"Current Status: {status['message']}")
    print(f"Cookie JSON Path: {status['path']}\n")

    path, count = interactive_login()
    print(f"\n[DONE] Saved {count} cookies to: {path}")


if __name__ == "__main__":
    main()
