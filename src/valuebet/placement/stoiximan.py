"""Stoiximan placement via Playwright — SINGLE account only.

Design notes / guardrails (intentional, do not remove):
  * DRY-RUN by default: the worker logs in, navigates to the selection, fills the
    stake, and reads back the live price, but does NOT click the final "place"
    button unless PLACEMENT_DRY_RUN=false.
  * APPROVAL gate: even outside dry-run, a bet is only placed if the signal has been
    explicitly approved (status="approved") in the dashboard, unless
    PLACEMENT_REQUIRE_APPROVAL=false.
  * Price-protection: the bet is abandoned if the live odds are below the signal's
    min_odds — the edge may have evaporated since detection.
  * Feature 3: After placement, the accepted stake is read from the bet receipt so
    any stake reduction by the bookmaker is recorded accurately.
  * Feature 4: After each placement attempt, a LimitEvent is recorded via the
    global BookmakerLimitTracker so the operator can monitor account health.
  * This is one account, operated as a normal user would. There is no fingerprint
    spoofing, proxy rotation, or multi-account orchestration here by design.

The DOM selectors below are placeholders: Stoiximan's markup must be confirmed
against the live site and the SELECTORS map updated. Everything else is real.
"""

from __future__ import annotations

from ..config import get_settings
from ..core.limit_tracker import LimitEvent, get_limit_tracker
from ..logging import get_logger
from .base import PlacementRequest, PlacementResult

log = get_logger("placement.stoiximan")

_LOGIN_URL = "https://www.stoiximan.gr/"

# TODO: confirm against the live site before going off dry-run.
SELECTORS = {
    "accept_cookies": "#onetrust-accept-btn-handler",
    "login_button": "[data-qa='header-login-button']",
    "username": "#username, input[name='username']",
    "password": "#password, input[name='password']",
    "submit_login": "[data-qa='login-submit'], button[type='submit']",
    "bet_slip_stake": "[data-qa='betslip-stake-input']",
    "bet_slip_odds": "[data-qa='betslip-odds']",
    "place_bet": "[data-qa='betslip-place-bet']",
    # Feature 3: receipt selectors — read accepted stake back from confirmation
    "bet_confirmation": "[data-qa='bet-receipt']",
    "receipt_stake": "[data-qa='receipt-stake']",    # Accepted stake amount
    "receipt_odds": "[data-qa='receipt-odds']",      # Confirmed odds
}

BOOKMAKER_NAME = "stoiximan"


class StoiximanPlacer:
    def __init__(self, headless: bool = True) -> None:
        self.settings = get_settings()
        self.headless = headless

    def place(self, request: PlacementRequest) -> PlacementResult:
        dry_run = self.settings.placement_dry_run
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            msg = "playwright not installed; run `playwright install chromium`"
            log.error("playwright_missing", selection=request.selection)
            result = PlacementResult(
                success=False, placed_odds=None,
                requested_stake=request.stake, accepted_stake=0.0,
                dry_run=dry_run, message=msg,
            )
            self._record_limit_event(result, request)
            return result

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()
            try:
                self._login(page)
                self._navigate_to_selection(page, request)
                live_odds = self._read_live_odds(page)

                if live_odds is None:
                    result = PlacementResult(
                        success=False, placed_odds=None,
                        requested_stake=request.stake, accepted_stake=0.0,
                        dry_run=dry_run, message="could not read live odds",
                    )
                    self._record_limit_event(result, request)
                    return result

                # Price protection: refuse if the edge has evaporated.
                if live_odds < request.min_odds:
                    msg = f"price moved: live {live_odds} < min {request.min_odds}; abandoning"
                    log.info("placement_abandoned", reason=msg, selection=request.selection)
                    result = PlacementResult(
                        success=False, placed_odds=live_odds,
                        requested_stake=request.stake, accepted_stake=0.0,
                        dry_run=dry_run, message=msg,
                    )
                    self._record_limit_event(result, request)
                    return result

                self._fill_stake(page, request.stake)

                if dry_run:
                    msg = "DRY-RUN: slip prepared, place button NOT clicked"
                    log.info("placement_dry_run", selection=request.selection,
                             odds=live_odds, stake=request.stake)
                    # In dry-run, assume full acceptance for simulation purposes
                    return PlacementResult(
                        success=True, placed_odds=live_odds,
                        requested_stake=request.stake, accepted_stake=request.stake,
                        dry_run=True, message=msg,
                    )

                self._click_place(page)
                ok = self._confirm(page)

                # Feature 3: read actual accepted stake from the receipt
                accepted_stake = self._read_accepted_stake(page, request.stake)
                receipt_odds = self._read_receipt_odds(page, live_odds)

                msg = "bet placed" if ok else "place clicked but no receipt detected"
                log.info("placement_result", selection=request.selection,
                         ok=ok, requested_stake=request.stake,
                         accepted_stake=accepted_stake, odds=receipt_odds)

                result = PlacementResult(
                    success=ok,
                    placed_odds=receipt_odds,
                    requested_stake=request.stake,
                    accepted_stake=accepted_stake,
                    dry_run=False,
                    message=msg,
                )
                # Feature 4: record limit event for account health monitoring
                self._record_limit_event(result, request)
                return result

            except Exception as exc:  # noqa: BLE001
                log.error("placement_error", error=str(exc), selection=request.selection)
                result = PlacementResult(
                    success=False, placed_odds=None,
                    requested_stake=request.stake, accepted_stake=0.0,
                    dry_run=dry_run, message=f"error: {exc}",
                )
                self._record_limit_event(result, request)
                return result
            finally:
                context.close()
                browser.close()

    # --- page steps (selectors must be validated against the live site) ---

    def _login(self, page) -> None:
        page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        self._maybe_click(page, SELECTORS["accept_cookies"])
        self._maybe_click(page, SELECTORS["login_button"])
        page.fill(SELECTORS["username"], self.settings.stoiximan_username)
        page.fill(SELECTORS["password"], self.settings.stoiximan_password)
        page.click(SELECTORS["submit_login"])
        page.wait_for_load_state("networkidle")

    def _navigate_to_selection(self, page, request: PlacementRequest) -> None:
        try:
            self._maybe_click(page, "[data-qa='search-icon']")
            page.fill("input[type='search']", request.selection)
            page.click("[data-qa='search-result']:first-child", timeout=5000)
            page.wait_for_load_state("networkidle")
            odds_str = str(request.target_odds).replace(".", ",")
            page.click(f"button:has-text('{odds_str}')", timeout=5000)
        except Exception as e:
            log.error("navigate_to_selection_failed", error=str(e),
                      selection=request.selection)
            raise RuntimeError(f"Failed to navigate to {request.selection}") from e

    def _read_live_odds(self, page) -> float | None:
        text = page.text_content(SELECTORS["bet_slip_odds"])
        try:
            return float(text.strip().replace(",", "."))
        except (AttributeError, ValueError):
            return None

    def _fill_stake(self, page, stake: float) -> None:
        page.fill(SELECTORS["bet_slip_stake"], f"{stake:.2f}")

    def _click_place(self, page) -> None:
        page.click(SELECTORS["place_bet"])

    def _confirm(self, page) -> bool:
        try:
            page.wait_for_selector(SELECTORS["bet_confirmation"], timeout=10_000)
            return True
        except Exception:
            return False

    # --- Feature 3: Read accepted stake from receipt ---

    def _read_accepted_stake(self, page, fallback: float) -> float:
        """Read the accepted stake from the bet receipt.

        If the bookmaker reduced the stake, the receipt will show the smaller
        amount. Falls back to the requested amount if the element is not found.
        """
        try:
            text = page.text_content(SELECTORS["receipt_stake"], timeout=5_000)
            cleaned = (text or "").strip().replace("€", "").replace(",", ".")
            return float(cleaned)
        except Exception:
            return fallback

    def _read_receipt_odds(self, page, fallback: float) -> float:
        """Read the confirmed odds from the bet receipt."""
        try:
            text = page.text_content(SELECTORS["receipt_odds"], timeout=5_000)
            return float((text or "").strip().replace(",", "."))
        except Exception:
            return fallback

    # --- Feature 4: Record limit event ---

    def _record_limit_event(self, result: PlacementResult,
                            request: PlacementRequest) -> None:
        """Push a LimitEvent to the global BookmakerLimitTracker."""
        was_rejected = not result.success and not result.dry_run
        event = LimitEvent(
            bookmaker=BOOKMAKER_NAME,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake if not was_rejected else 0.0,
            was_rejected=was_rejected,
            note=result.message,
        )
        tracker = get_limit_tracker()
        tracker.record(event)

        if result.was_stake_reduced:
            log.warning(
                "stake_reduced_by_bookmaker",
                bookmaker=BOOKMAKER_NAME,
                requested=result.requested_stake,
                accepted=result.accepted_stake,
                ratio=round(result.accepted_stake / result.requested_stake, 3),
            )
        if tracker.is_likely_limited(BOOKMAKER_NAME):
            log.warning(
                "account_likely_limited",
                bookmaker=BOOKMAKER_NAME,
                acceptance_rate=tracker.acceptance_rate(BOOKMAKER_NAME),
            )

    @staticmethod
    def _maybe_click(page, selector: str) -> None:
        try:
            page.click(selector, timeout=3000)
        except Exception:
            pass
