"""GoldEdge Integration and Scraper (https://www.goldedge.net/).

Allows importing value betting opportunities from GoldEdge during their free beta access:
1. Interactive session login to save auth cookies (`python -m valuebet.cli login-goldedge`).
2. Headless scraping of value bets (`python -m valuebet.cli sync-goldedge`).
3. Normalization into internal ValueSignal objects for Stoiximan/Betano placement.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..core.models import SettlementRule, Sport, ValueSignal
from ..core.odds_math import edge as calc_edge, kelly_stake
from ..db.repository import save_signal
from ..db.session import session_scope
from ..logging import get_logger
from ..placement.session_store import (
    load_cookies_into_context,
    save_cookies_from_context,
)

log = get_logger("sources.goldedge")

_BASE_URL = "https://www.goldedge.net"


def get_goldedge_cookie_path(custom_path: str | Path | None = None) -> Path:
    if custom_path is not None:
        target = Path(custom_path)
    else:
        settings = get_settings()
        target = Path(getattr(settings, "goldedge_cookie_path", "data/goldedge_cookies.json"))
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def interactive_login(
    url: str = _BASE_URL,
    cookie_path: str | Path | None = None,
    timeout_seconds: int = 180,
) -> tuple[Path, int]:
    """Launch an interactive (visible) browser session for the operator to log in to GoldEdge.

    Saves session cookies to disk so automated background scrapers can access
    the protected Valuebets section without prompting for passwords.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright is required for GoldEdge login. Install with `pip install playwright`.")

    path = get_goldedge_cookie_path(cookie_path)
    log.info("goldedge_interactive_login_started", url=url, path=str(path))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )

        load_cookies_into_context(context, path)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print("\n" + "=" * 70)
        print("          GOLDEDGE INTERACTIVE LOGIN SESSION")
        print("=" * 70)
        print(f"1. A browser window has opened to {url}")
        print("2. Please click 'Log In' / 'Get access' and sign in to your GoldEdge account.")
        print("3. Once logged in (your avatar appears in the top right), return here.")
        print(f"4. Press ENTER in this console to capture and save session cookies.")
        print("=" * 70 + "\n")

        try:
            input("Press [ENTER] after completing login in the browser...")
        except (EOFError, KeyboardInterrupt):
            pass

        count = save_cookies_from_context(context, path)
        browser.close()

    log.info("goldedge_interactive_login_complete", path=str(path), cookies_saved=count)
    return path, count


class GoldEdgeSource:
    """Scrapes value betting signals from GoldEdge and normalizes them into ValueSignals."""

    name = "goldedge"

    def __init__(self, headless: bool = True, cookie_path: str | Path | None = None) -> None:
        self.headless = headless
        self.cookie_path = get_goldedge_cookie_path(cookie_path)
        self._settings = get_settings()

    def fetch_value_bets(self, min_edge: float | None = None) -> list[ValueSignal]:
        """Scrape current value bets from GoldEdge dashboard and persist as ValueSignals."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright_missing", source="goldedge")
            return []

        effective_min_edge = min_edge if min_edge is not None else getattr(self._settings, "goldedge_min_edge", 0.02)
        signals: list[ValueSignal] = []
        now = datetime.now(UTC)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                )

                if self.cookie_path.exists():
                    load_cookies_into_context(context, self.cookie_path)

                page = context.new_page()
                # Navigate to GoldEdge home or valuebets
                page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=20000)
                time.sleep(2.0)

                # Check if Valuebets link is available in navbar
                vb_link = page.locator("a:has-text('Valuebets'), button:has-text('Valuebets')").first
                if vb_link.count() > 0:
                    try:
                        vb_link.click()
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        time.sleep(2.0)
                    except Exception:
                        pass

                # Extract table rows or signal cards
                # Selectors cover standard GoldEdge value bet rows/tables
                rows = page.locator("tr, .value-bet-row, .vb-card, [data-row-id]").all()
                log.info("goldedge_scanned_elements", count=len(rows), url=page.url)

                for idx, row in enumerate(rows):
                    text = row.inner_text().strip()
                    if not text or "Pinnacle" not in text and "%" not in text:
                        continue

                    parsed_sig = self._parse_row_text(text, idx, now, effective_min_edge)
                    if parsed_sig:
                        signals.append(parsed_sig)

                browser.close()
        except Exception as exc:
            log.error("goldedge_scrape_error", error=str(exc))

        log.info("goldedge_signals_extracted", count=len(signals))
        return signals

    def _parse_row_text(self, text: str, idx: int, now: datetime, min_edge: float) -> ValueSignal | None:
        """Parse text of a GoldEdge valuebet row into a ValueSignal."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 3:
            return None

        # Look for percentage (Edge), e.g. "+3.45%" or "3.5%"
        edge_val = None
        for line in lines:
            m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", line)
            if m:
                try:
                    edge_val = float(m.group(1)) / 100.0
                    break
                except ValueError:
                    continue

        if edge_val is None or edge_val < min_edge:
            return None

        # Look for odds (numbers with decimals like 1.85, 2.10)
        odds_candidates = []
        for line in lines:
            m = re.findall(r"\b(\d+\.\d{2})\b", line)
            for val in m:
                try:
                    f = float(val)
                    if 1.05 <= f <= 20.0:
                        odds_candidates.append(f)
                except ValueError:
                    continue

        if len(odds_candidates) < 2:
            return None

        # Usually offered bookmaker odds and sharp reference odds
        offered_odds = odds_candidates[0]
        sharp_odds = odds_candidates[1]

        # Fair prob estimated from sharp odds
        fair_prob = 1.0 / sharp_odds if sharp_odds > 1.0 else 0.5

        # Infer selection and match name
        event_name = lines[0] if len(lines) > 0 else f"GoldEdge Match {idx}"
        selection = lines[1] if len(lines) > 1 else "Selection"

        # Kelly stake
        bankroll = self._settings.bankroll
        kelly_frac = self._settings.kelly_fraction
        max_stake = self._settings.max_stake
        stake = kelly_stake(fair_prob, offered_odds, bankroll=bankroll, fraction=kelly_frac, max_stake=max_stake)

        target_book = self._settings.placement_bookmaker or "stoiximan"

        return ValueSignal(
            event_id=f"ge_{idx}_{int(now.timestamp())}",
            market_id=f"ge_m_{idx}",
            market_type="MATCH_ODDS",
            selection=selection,
            sport=Sport.SOCCER,
            fair_prob=round(fair_prob, 4),
            confirm_prob=round(fair_prob, 4),
            target_bookmaker=target_book,
            target_odds=offered_odds,
            edge=round(edge_val, 4),
            recommended_stake=stake,
            detected_at=now,
            is_live=False,
            event_name=event_name,
            pinnacle_odds=sharp_odds,
            betfair_odds=sharp_odds,
        )


def sync_goldedge_to_db(min_edge: float | None = None) -> int:
    """Convenience function to fetch value bets from GoldEdge and save to DB."""
    source = GoldEdgeSource(headless=True)
    signals = source.fetch_value_bets(min_edge=min_edge)
    persisted = 0
    with session_scope() as session:
        for sig in signals:
            saved = save_signal(session, sig)
            if saved:
                persisted += 1
    log.info("goldedge_sync_complete", extracted=len(signals), persisted=persisted)
    return persisted

