"""Stoiximan real odds source via Playwright scraping.

Since Stoiximan lacks a public API, we use Playwright to load the sports
pages and extract the live odds from the DOM.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from ..core.models import MarketSnapshot, MarketStatus, Quote, SettlementRule, Sport
from ..logging import get_logger

log = get_logger("sources.stoiximan")

# Selectors need to be confirmed against the live site.
SELECTORS = {
    "match_row": ".match-row", # A container for a single match
    "event_id": "data-event-id", # Attribute containing the event ID
    "team_name": ".team-name", # Elements containing team names
    "odds_button": ".odds-button", # Elements containing odds
}

class StoiximanSource:
    name = "stoiximan"

    def __init__(self, headless: bool = True):
        self.headless = headless

    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright_missing", source="stoiximan")
            return []

        snapshots = []
        now = datetime.now(timezone.utc)
        
        # Build URL based on sport
        # In reality, this needs to be mapped to Stoiximan's exact URL paths.
        sport_path = sport.value.lower()
        if sport_path == "soccer":
            sport_path = "football"
            
        url = f"https://www.stoiximan.gr/sport/{sport_path}/"
        if live:
            url = f"https://www.stoiximan.gr/live/"

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded")
                
                # Wait for matches to load
                try:
                    page.wait_for_selector(SELECTORS["match_row"], timeout=10000)
                except Exception:
                    log.warning("stoiximan_no_matches_found", url=url)
                    browser.close()
                    return []

                # Scrape the DOM
                matches = page.locator(SELECTORS["match_row"]).all()
                for match in matches:
                    try:
                        event_id = match.get_attribute(SELECTORS["event_id"]) or "unknown"
                        teams = match.locator(SELECTORS["team_name"]).all_text_contents()
                        odds = match.locator(SELECTORS["odds_button"]).all_text_contents()
                        
                        if len(teams) >= 2 and len(odds) >= 3: # Assuming 1X2 market
                            quotes = [
                                Quote(self.name, teams[0].strip(), self._parse_odds(odds[0]), now),
                                Quote(self.name, "Draw", self._parse_odds(odds[1]), now),
                                Quote(self.name, teams[1].strip(), self._parse_odds(odds[2]), now),
                            ]
                            
                            snapshots.append(
                                MarketSnapshot(
                                    event_id=event_id,
                                    market_id=event_id,
                                    market_type="MATCH_ODDS",
                                    sport=sport,
                                    status=MarketStatus.LIVE if live else MarketStatus.PREMATCH,
                                    start_time=now, # Real start time needs to be scraped
                                    quotes=quotes,
                                    settlement_rule=SettlementRule.REGULATION_TIME, # Feature 5
                                )
                            )
                    except Exception as e:
                        log.debug("stoiximan_parse_error", error=str(e))
                        
                browser.close()
        except Exception as e:
            log.error("stoiximan_fetch_error", error=str(e))

        return snapshots

    def _parse_odds(self, text: str) -> float:
        try:
            return float(text.strip().replace(",", "."))
        except ValueError:
            return 1.0
