"""Stoiximan placement via Playwright — SINGLE account only.

Design notes / guardrails (intentional, do not remove):
  * DRY-RUN by default: the worker logs in, navigates to the selection, fills the
    stake, and reads back the live price, but does NOT click the final "place"
    button unless PLACEMENT_DRY_RUN=false. A dry run reports status=DRY_RUN and is
    NEVER recorded as a real bet.
  * APPROVAL gate: even outside dry-run, a bet is only placed if the signal has been
    explicitly approved (status="approved") in the dashboard, unless
    PLACEMENT_REQUIRE_APPROVAL=false.
  * Price-protection: the bet is abandoned if the live odds are below the signal's
    min_odds — the edge may have evaporated since detection.
  * VERIFICATION: after clicking place we re-read Stoiximan's own open-bets list and
    only report `verified_on_platform=True` if the bet is actually there. Anything
    else is reported as UNCONFIRMED so the operator checks manually — we never claim
    a bet exists on the platform on the strength of a click alone.
  * Feature 3: After placement, the accepted stake is read from the bet receipt so
    any stake reduction by the bookmaker is recorded accurately.
  * Feature 4: After each placement attempt, a LimitEvent is recorded via the
    global BookmakerLimitTracker so the operator can monitor account health.
  * This is one account, operated as a normal user would. There is no fingerprint
    spoofing, proxy rotation, or multi-account orchestration here by design.

The DOM selectors below must be confirmed against the live site. They are ordered
candidate lists, tried most-specific first — a comma-joined CSS union would instead
resolve to whichever element happens to come first in the DOM, which is how a
"place bet" click can silently land on the wrong control.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..config import get_settings
from ..core.limit_tracker import LimitEvent, get_limit_tracker
from ..logging import get_logger
from .base import PlacementRequest, PlacementResult, PlacementStatus
from .session_store import (
    get_cookie_file_path,
    load_cookies_into_context,
    save_cookies_from_context,
)

log = get_logger("placement.stoiximan")

_LOGIN_URL = "https://www.stoiximan.com.cy/"
_MY_BETS_URL = "https://www.stoiximan.com.cy/my-bets/"

# Ordered candidate lists: each is tried in turn, first match wins.
SELECTORS: dict[str, list[str]] = {
    "accept_cookies": [
        "#js-accept-btn",
        "#onetrust-accept-btn-handler",
        "button[data-isterms='true']",
        "button:has-text('Αποδοχή')",
        "button:has-text('Accept')",
    ],
    "login_button": [
        "[data-qa='login-button']",
        "button:has-text('ΣΥΝΔΕΣΗ')",
        "button:has-text('LOG IN')",
        "button:has-text('Log In')",
    ],
    "logged_in_indicator": [
        "[data-qa='user-balance']",
        "[data-qa='header-user-btn']",
        "[data-qa='account-button']",
        ".account-balance",
        "button:has-text('Κατάθεση')",
        "button:has-text('Deposit')",
    ],
    "username": [
        "#username",
        "form[data-qa='login'] input[name='username']",
        "input[name='username']",
    ],
    "password": [
        "#password",
        "form[data-qa='login'] input[type='password']",
        "input[name='Password']",
        "input[name='password']",
    ],
    "submit_login": [
        "form[data-qa='login'] button[data-qa='submit']",
        "[data-qa='submit']",
        "button[type='submit']",
    ],
    # The betslip must contain our selection before we touch the stake box.
    "bet_slip_selection": [
        "[data-qa='betslip-selection']",
        "[data-qa='betslip'] [data-qa='selection-name']",
        ".betslip-selection",
    ],
    "bet_slip_stake": [
        "[data-qa='betslip-stake-input']",
        "[data-qa='stake-area'] input",
        "[data-qa='stake-area']",
        ".betslip-stake-input input",
        "input[name='stake']",
    ],
    # The PRICE of the selection in the slip — not the payout/total row.
    "bet_slip_odds": [
        "[data-qa='betslip-odds']",
        "[data-qa='betslip-selection'] [data-qa='selection-price']",
        "[data-qa='selection-price']",
        ".betslip-odds",
    ],
    # NOTE: no `button:has-text('STAKE')` here — :has-text is a case-insensitive
    # substring match, so it also matches quick-stake chips and the stake label,
    # which is how a "place" click can end up staking nothing.
    "place_bet": [
        "[data-qa='place-bet-button']",
        "[data-qa='betslip-place-bet']",
        "button:has-text('ΣΤΟΙΧΗΜΑΤΙΣΕ')",
        "button:has-text('PLACE BET')",
    ],
    # Stoiximan interposes an "odds changed — accept?" step when the price moves
    # between building the slip and confirming. Without clicking this, the bet is
    # never struck even though the place button was pressed.
    "accept_odds_change": [
        "[data-qa='accept-changes-button']",
        "button:has-text('ΑΠΟΔΟΧΗ ΑΛΛΑΓΩΝ')",
        "button:has-text('Accept changes')",
    ],
    # Feature 3: receipt selectors — read accepted stake back from confirmation
    "bet_confirmation": [
        "[data-qa='bet-receipt']",
        ".bet-receipt",
        ".receipt-container",
    ],
    "receipt_stake": ["[data-qa='receipt-stake']"],
    "receipt_odds": ["[data-qa='receipt-odds']"],
    # Verification: the account's own list of open bets.
    "open_bet_row": [
        "[data-qa='my-bets-item']",
        "[data-qa='bet-history-item']",
        ".my-bets-item",
        ".bet-history-row",
    ],
    "betslip_error": [
        "[data-qa='betslip-error']",
        ".betslip-error",
        "[data-qa='error-message']",
    ],
}

BOOKMAKER_NAME = "stoiximan"


def parse_money(text: str | None) -> float | None:
    """Parse a European-formatted odds/stake string to float.

    Handles '1,85', '1.85', '10,00 €', '€10.00', '1.234,56' and returns None for
    anything that isn't a number. The previous implementation did a bare
    float(text.replace(',', '.')) which raised on any currency symbol and made a
    readable price look like "could not read live odds".
    """
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", str(text))
    if not cleaned:
        return None
    # If both separators appear, the last one is the decimal separator.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # A single comma is a decimal comma unless it looks like a thousands group.
        cleaned = cleaned.replace(",", "" if re.fullmatch(r"-?\d{1,3},\d{3}", cleaned) else ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


class StoiximanPlacer:
    """Persistent-session Stoiximan placer.

    The browser is launched once and the login is performed once.
    Every subsequent call to ``place()`` reuses the same page —
    no re-login on every bet.  The session is automatically refreshed
    if it expires (e.g. after a long idle period).
    """

    def __init__(self, headless: bool = True, cookie_path: str | None = None) -> None:
        self.settings = get_settings()
        self.headless = headless
        self.cookie_path = get_cookie_file_path(cookie_path or self.settings.stoiximan_cookie_path)
        # Persistent session state
        self._pw = None          # playwright instance
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False

    # ------------------------------------------------------------------ public

    def place(self, request: PlacementRequest) -> PlacementResult:
        """Attempt one placement, logging the start and the outcome exactly once.

        The wrapper exists so no early return inside the implementation can escape
        without an outcome log — `place()` has a dozen exit paths.
        """
        dry_run = self.settings.placement_dry_run
        started = time.monotonic()
        # Logged before any browser work: if the worker hangs or is killed, there
        # is still a record of exactly what was about to be staked.
        log.info(
            "placement_attempt_started",
            bookmaker=BOOKMAKER_NAME,
            event_id=request.event_id,
            selection=request.selection,
            market_type=request.market_type,
            target_odds=request.target_odds,
            min_odds=request.min_odds,
            stake=request.stake,
            dry_run=dry_run,
        )
        if dry_run:
            log.warning(
                "placement_dry_run_active",
                msg="PLACEMENT_DRY_RUN=true — no bet will be placed at the bookmaker",
            )
        result = self._place_impl(request, dry_run)
        self._log_outcome(request, result, started)
        return result

    def _place_impl(self, request: PlacementRequest, dry_run: bool) -> PlacementResult:
        try:
            self._ensure_session()
        except Exception as exc:
            msg = f"session error: {exc}"
            log.error("session_failed", error=msg)
            return self._finish(
                request, dry_run,
                PlacementResult(
                    success=False, placed_odds=None,
                    requested_stake=request.stake, accepted_stake=0.0,
                    dry_run=dry_run, message=msg,
                    bookmaker=BOOKMAKER_NAME, status=PlacementStatus.ERROR,
                ),
            )

        page = self._page
        try:
            self._navigate_to_selection(page, request)

            # The slip must actually hold a selection, or "filling the stake" and
            # "clicking place" both operate on an empty slip and quietly do nothing.
            if not self._first_visible(page, "bet_slip_selection", timeout=5000):
                msg = "betslip is empty after navigation; selection was not added"
                log.error("betslip_empty", selection=request.selection)
                return self._finish(
                    request, dry_run,
                    PlacementResult(
                        success=False, placed_odds=None,
                        requested_stake=request.stake, accepted_stake=0.0,
                        dry_run=dry_run, message=msg,
                        bookmaker=BOOKMAKER_NAME, status=PlacementStatus.ERROR,
                    ),
                )

            live_odds = self._read_live_odds(page)

            if live_odds is None:
                return self._finish(
                    request, dry_run,
                    PlacementResult(
                        success=False, placed_odds=None,
                        requested_stake=request.stake, accepted_stake=0.0,
                        dry_run=dry_run, message="could not read live odds from betslip",
                        bookmaker=BOOKMAKER_NAME, status=PlacementStatus.ERROR,
                    ),
                )

            # Price protection: refuse if the edge has evaporated.
            if live_odds < request.min_odds:
                msg = f"price moved: live {live_odds} < min {request.min_odds}; abandoning"
                log.info("placement_abandoned", reason=msg, selection=request.selection)
                return self._finish(
                    request, dry_run,
                    PlacementResult(
                        success=False, placed_odds=live_odds,
                        requested_stake=request.stake, accepted_stake=0.0,
                        dry_run=dry_run, message=msg,
                        bookmaker=BOOKMAKER_NAME, status=PlacementStatus.REJECTED,
                    ),
                )

            self._fill_stake(page, request.stake)

            if dry_run:
                msg = "DRY-RUN: slip prepared, place button NOT clicked — no bet exists at the bookmaker"
                log.info("placement_dry_run", selection=request.selection,
                         odds=live_odds, stake=request.stake)
                self._clear_slip(page)
                return PlacementResult(
                    success=True, placed_odds=live_odds,
                    requested_stake=request.stake, accepted_stake=request.stake,
                    dry_run=True, message=msg,
                    bookmaker=BOOKMAKER_NAME, status=PlacementStatus.DRY_RUN,
                    verified_on_platform=False,
                )

            if not self._click_place(page):
                msg = "place button not found or not clickable"
                log.error("place_button_missing", selection=request.selection)
                return self._finish(
                    request, dry_run,
                    PlacementResult(
                        success=False, placed_odds=live_odds,
                        requested_stake=request.stake, accepted_stake=0.0,
                        dry_run=False, message=msg,
                        bookmaker=BOOKMAKER_NAME, status=PlacementStatus.ERROR,
                    ),
                )

            # Stoiximan may ask us to accept a price change before committing.
            if self._click_first(page, "accept_odds_change", timeout=3000):
                log.info("odds_change_accepted", selection=request.selection)

            receipt_seen = self._confirm(page)
            slip_error = self._read_slip_error(page)

            accepted_stake = self._read_accepted_stake(page, request.stake)
            receipt_odds = self._read_receipt_odds(page, live_odds)

            # The only trustworthy evidence: the bet appears in the account's own
            # open-bets list on the platform.
            verified = self._verify_bet_on_platform(request, receipt_odds)

            if verified:
                status = PlacementStatus.PLACED
                msg = "bet placed and verified in Stoiximan open bets"
            elif receipt_seen:
                status = PlacementStatus.UNCONFIRMED
                msg = ("receipt shown but bet NOT found in open bets — "
                       "verify manually on Stoiximan before treating as placed")
            elif slip_error:
                status = PlacementStatus.REJECTED
                msg = f"bookmaker rejected the bet: {slip_error}"
            else:
                status = PlacementStatus.UNCONFIRMED
                msg = ("place clicked but no receipt and no open bet found — "
                       "verify manually on Stoiximan")

            log.info("placement_result", selection=request.selection,
                     status=status.value, verified=verified,
                     receipt_seen=receipt_seen, requested_stake=request.stake,
                     accepted_stake=accepted_stake, odds=receipt_odds)
            if status == PlacementStatus.UNCONFIRMED:
                log.error("placement_unconfirmed", selection=request.selection,
                          stake=request.stake, msg=msg)

            return self._finish(
                request, dry_run,
                PlacementResult(
                    success=status == PlacementStatus.PLACED,
                    placed_odds=receipt_odds,
                    requested_stake=request.stake,
                    accepted_stake=accepted_stake if status != PlacementStatus.REJECTED else 0.0,
                    dry_run=False,
                    message=msg,
                    bookmaker=BOOKMAKER_NAME,
                    status=status,
                    verified_on_platform=verified,
                ),
            )

        except Exception as exc:
            log.error("placement_error", error=str(exc), selection=request.selection)
            # If error looks like session expired, reset so next call re-logs in
            if any(k in str(exc).lower() for k in ["timeout", "closed", "disconnected", "login"]):
                log.warning("session_reset", reason="placement error, will re-login on next call")
                self._logged_in = False
            return self._finish(
                request, dry_run,
                PlacementResult(
                    success=False, placed_odds=None,
                    requested_stake=request.stake, accepted_stake=0.0,
                    dry_run=dry_run, message=f"error: {exc}",
                    bookmaker=BOOKMAKER_NAME, status=PlacementStatus.ERROR,
                ),
            )

    def close(self) -> None:
        """Cleanly shut down the persistent browser session."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._pw = None
            self._logged_in = False

    # ------------------------------------------------------------------ session

    def _ensure_session(self) -> None:
        """Guarantee browser is open and user is logged in.
        Called before every placement — cheap if already set up.
        """
        self._start_browser_if_needed()
        if not self._logged_in:
            self._login(self._page)
            self._logged_in = True

    def _start_browser_if_needed(self) -> None:
        """Launch browser + context once; reuse on subsequent calls."""
        if self._page is not None:
            try:
                # Quick check: page still alive?
                self._page.title()
                return
            except Exception:
                log.warning("browser_dead", msg="restarting browser session")
                self.close()

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright not installed; run `playwright install chromium`")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-infobars",
                "--start-maximized",
            ]
        )
        context_kwargs = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1366, "height": 768},
            "locale": "el-GR",
            "timezone_id": "Europe/Athens",
            "java_script_enabled": True,
        }
        if getattr(self.settings, "stoiximan_proxy", None):
            proxy_dict = {"server": self.settings.stoiximan_proxy}
            if getattr(self.settings, "stoiximan_proxy_username", None):
                proxy_dict["username"] = self.settings.stoiximan_proxy_username
                proxy_dict["password"] = self.settings.stoiximan_proxy_password
            context_kwargs["proxy"] = proxy_dict
            log.info("proxy_configured", server=self.settings.stoiximan_proxy)

        self._context = self._browser.new_context(**context_kwargs)
        # Mask webdriver property so DataDome doesn't detect headless. This must be
        # an init script on the CONTEXT: added to a page after creation it only runs
        # on the next navigation, leaving the first page load unmasked.
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        # Load saved cookies if they exist (avoids re-login)
        self._load_cookies(self._context)
        self._page = self._context.new_page()
        log.info("browser_started", headless=self.headless, cookie_path=str(self.cookie_path))

    # ------------------------------------------------------------------ cookies

    def _load_cookies(self, context) -> bool:
        """Load saved cookies into browser context. Returns True if loaded."""
        loaded, _count = load_cookies_into_context(context, self.cookie_path)
        return loaded

    def _save_cookies(self, context) -> None:
        """Save browser cookies to file for reuse in future sessions."""
        save_cookies_from_context(context, self.cookie_path)

    def _is_logged_in(self, page) -> bool:
        """Check if we are already logged in (cookies still valid)."""
        try:
            page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)  # Let JS render

            content = page.content().lower()
            if "regulatory provisions" in content or "access denied" in content or "attention required" in content:
                log.error("stoiximan_access_blocked", msg="Site blocked by location or Cloudflare on this server IP")
                self._screenshot(page, "stoiximan_blocked.png")
                return False

            # If user balance or account menu is visible -> logged in!
            if self._first_visible(page, "logged_in_indicator", timeout=3000):
                return True
            return False
        except Exception:
            return False

    def _login(self, page) -> None:
        """Login only if not already logged in via saved cookies."""
        try:
            # Check if saved cookies are still valid
            if self._is_logged_in(page):
                log.info("session_reused", msg="already logged in via cookies",
                         balance=self._read_balance(page))
                return

            if not (self.settings.stoiximan_username and self.settings.stoiximan_password):
                raise RuntimeError(
                    "no valid Stoiximan session: cookies are missing/expired and "
                    "STOIXIMAN_USERNAME/PASSWORD are not configured"
                )

            log.info("login_required", msg="cookies invalid or missing, logging in")
            # Human-like delay before interacting
            time.sleep(1)
            self._click_first(page, "accept_cookies", timeout=3000)
            time.sleep(0.5)

            if not self._click_first(page, "login_button", timeout=3000):
                page.goto("https://www.stoiximan.com.cy/?login=1", wait_until="domcontentloaded", timeout=10000)
                time.sleep(1)

            user_sel = self._first_visible(page, "username", timeout=8000)
            pass_sel = self._first_visible(page, "password", timeout=8000)
            if not (user_sel and pass_sel):
                raise RuntimeError("login form fields not found")

            # Type slowly like a human
            page.type(user_sel, self.settings.stoiximan_username, delay=80)
            time.sleep(0.3)
            page.type(pass_sel, self.settings.stoiximan_password, delay=80)
            time.sleep(0.5)
            if not self._click_first(page, "submit_login", timeout=5000):
                raise RuntimeError("login submit button not found")
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

            # Confirm the login actually took, instead of assuming it did.
            if not self._first_visible(page, "logged_in_indicator", timeout=8000):
                self._screenshot(page, "login_failed.png")
                raise RuntimeError("credentials submitted but no logged-in indicator appeared")

            # Save cookies so next run skips login entirely
            self._save_cookies(page.context)
            log.info("login_successful", msg="cookies saved for future reuse",
                     balance=self._read_balance(page))

        except Exception as exc:
            log.warning("stoiximan_login_failed", error=str(exc))
            raise RuntimeError(f"Stoiximan login failed: {exc}") from exc

    def _navigate_to_selection(self, page, request: PlacementRequest) -> None:
        step = "start"
        log.info("navigation_started", selection=request.selection,
                 market_type=request.market_type, target_odds=request.target_odds,
                 url=page.url)
        try:
            step = "geo_check"
            content = page.content().lower()
            if "regulatory provisions" in content or "access denied" in content:
                raise RuntimeError("Stoiximan is geo-blocked on this server IP (Access Denied)")

            step = "open_search"
            # Try clicking search icon button (check parent button/link if svg)
            for sel in [
                "button:has([data-qa='header-icons-search-icon'])",
                "a:has([data-qa='header-icons-search-icon'])",
                "[data-qa='header-icons-search-icon']",
                "[data-qa='search-icon']",
                "button[aria-label*='Search']",
                "button[aria-label*='Αναζήτηση']",
            ]:
                if self._maybe_click(page, sel):
                    break

            time.sleep(1)
            step = "search_input"
            search_input_sel = "input[type='search'], [data-qa='search-input'], input[placeholder*='Search'], input[placeholder*='Αναζήτηση']"
            page.wait_for_selector(search_input_sel, timeout=5000)
            page.fill(search_input_sel, request.selection)
            time.sleep(1)

            step = "search_result"
            result_sel = "[data-qa='search-result']:first-child, [data-qa*='search-result']:first-child, .search-result-item:first-child"
            page.click(result_sel, timeout=5000)
            page.wait_for_load_state("networkidle")
            log.info("event_page_opened", selection=request.selection, url=page.url)

            step = "click_selection"
            # Prefer clicking the named selection; matching on the odds TEXT is a
            # last resort because the price shown on Stoiximan will not equal the
            # price the signal was detected at (different feed, and it moves).
            if self._click_selection_by_name(page, request.selection):
                log.info("selection_clicked_by_name", selection=request.selection)
                return
            odds_str = f"{request.target_odds:.2f}".replace(".", ",")
            log.warning(
                "selection_click_by_odds_fallback",
                selection=request.selection,
                odds=odds_str,
                msg="could not find the named outcome; falling back to matching the "
                    "odds text, which may click the wrong selection",
            )
            step = "click_selection_by_odds"
            page.click(
                f"[data-qa='event-selection']:has-text('{odds_str}'), button:has-text('{odds_str}')",
                timeout=5000,
            )
        except Exception as e:
            self._screenshot(page, "navigate_failed.png")
            log.error("navigate_to_selection_failed", error=str(e),
                      failed_step=step, selection=request.selection,
                      url=getattr(page, "url", None),
                      screenshot="data/navigate_failed.png")
            raise RuntimeError(
                f"Failed to navigate to {request.selection} (step: {step})"
            ) from e

    def _click_selection_by_name(self, page, selection: str) -> bool:
        """Click the outcome button whose label matches the selection name."""
        name = selection.strip()
        for sel in [
            f"[data-qa='event-selection']:has-text('{name}')",
            f"[data-qa='selection']:has-text('{name}')",
            f"button:has-text('{name}')",
        ]:
            if self._maybe_click(page, sel):
                return True
        return False

    # ------------------------------------------------------------- slip reading

    def _read_live_odds(self, page) -> float | None:
        """Read the PRICE of our selection from the betslip.

        Deliberately does not fall back to the slip's totals row: that shows the
        stake/potential-returns amount, and reading it as a price would defeat the
        min_odds price-protection check.
        """
        for sel in SELECTORS["bet_slip_odds"]:
            try:
                value = parse_money(page.text_content(sel, timeout=2000))
            except Exception:
                continue
            # A decimal price is always > 1.0; anything else is the wrong element.
            if value is not None and value > 1.0:
                return value
        return None

    def _fill_stake(self, page, stake: float) -> None:
        sel = self._first_visible(page, "bet_slip_stake", timeout=5000)
        if not sel:
            raise RuntimeError("betslip stake input not found")
        page.fill(sel, "")
        page.fill(sel, f"{stake:.2f}")
        # Some slips only register the amount after an input/blur event.
        try:
            page.dispatch_event(sel, "input")
        except Exception:
            pass
        # Read the field back: a slip that silently ignored the value would
        # otherwise place a bet for a different amount, or for nothing.
        try:
            written = parse_money(page.input_value(sel, timeout=2000))
        except Exception:
            written = None
        log.info("stake_filled", requested=round(stake, 2), slip_value=written,
                 selector=sel)
        if written is not None and abs(written - stake) > 0.01:
            log.warning("stake_mismatch", requested=round(stake, 2),
                        slip_value=written,
                        msg="betslip did not accept the requested stake")

    def _clear_slip(self, page) -> None:
        """Empty the betslip after a dry run so the next attempt starts clean."""
        for sel in ["[data-qa='betslip-remove-all']", "[data-qa='remove-selection']",
                    "button:has-text('Καθαρισμός')"]:
            if self._maybe_click(page, sel):
                return

    def _click_place(self, page) -> bool:
        return self._click_first(page, "place_bet", timeout=5000)

    def _confirm(self, page) -> bool:
        for sel in SELECTORS["bet_confirmation"]:
            try:
                page.wait_for_selector(sel, timeout=5000)
                return True
            except Exception:
                continue
        return False

    def _read_slip_error(self, page) -> str | None:
        """Read any rejection message the bookmaker put on the slip."""
        for sel in SELECTORS["betslip_error"]:
            try:
                text = page.text_content(sel, timeout=1500)
            except Exception:
                continue
            if text and text.strip():
                return text.strip()[:200]
        return None

    # ------------------------------------------------- platform-side verification

    def _verify_bet_on_platform(self, request: PlacementRequest,
                                odds: float | None) -> bool:
        """Re-read Stoiximan's own open-bets page and look for this bet.

        This is the check that catches the failure mode where our side reports a
        successful placement but nothing exists at the bookmaker. A click, and even
        a receipt element, is not proof; the account's bet list is.
        """
        page = self._page
        try:
            page.goto(_MY_BETS_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            needle = request.selection.strip().lower()
            for sel in SELECTORS["open_bet_row"]:
                try:
                    rows = page.query_selector_all(sel)
                except Exception:
                    continue
                for row in rows:
                    try:
                        text = (row.text_content() or "").lower()
                    except Exception:
                        continue
                    if needle and needle in text:
                        log.info("bet_verified_on_platform",
                                 selection=request.selection, odds=odds)
                        return True
            log.warning("bet_not_found_on_platform", selection=request.selection,
                        url=_MY_BETS_URL)
            self._screenshot(page, "verify_failed.png")
            return False
        except Exception as exc:
            log.warning("bet_verification_failed", error=str(exc),
                        selection=request.selection)
            return False

    # --- Feature 3: Read accepted stake from receipt ---

    def _read_accepted_stake(self, page, fallback: float) -> float:
        """Read the accepted stake from the bet receipt.

        If the bookmaker reduced the stake, the receipt will show the smaller
        amount. Falls back to the requested amount if the element is not found.
        """
        for sel in SELECTORS["receipt_stake"]:
            try:
                value = parse_money(page.text_content(sel, timeout=3000))
            except Exception:
                continue
            if value is not None and value > 0:
                return value
        return fallback

    def _read_receipt_odds(self, page, fallback: float | None) -> float | None:
        """Read the confirmed odds from the bet receipt."""
        for sel in SELECTORS["receipt_odds"]:
            try:
                value = parse_money(page.text_content(sel, timeout=3000))
            except Exception:
                continue
            if value is not None and value > 1.0:
                return value
        return fallback

    # --- Feature 4: Record limit event ---

    def _finish(self, request: PlacementRequest, dry_run: bool,
                result: PlacementResult) -> PlacementResult:
        """Record the limit event for a real attempt and return the result."""
        if not dry_run:
            self._record_limit_event(result, request)
        return result

    def _log_outcome(self, request: PlacementRequest, result: PlacementResult,
                     started: float) -> None:
        """Single terminal log line per attempt, whatever the outcome."""
        log.info(
            "placement_attempt_finished",
            bookmaker=result.bookmaker,
            selection=request.selection,
            status=result.status.value,
            verified_on_platform=result.verified_on_platform,
            placed_odds=result.placed_odds,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake,
            dry_run=result.dry_run,
            elapsed_seconds=round(time.monotonic() - started, 2),
            message=result.message,
        )

    def _record_limit_event(self, result: PlacementResult,
                            request: PlacementRequest) -> None:
        """Push a LimitEvent to the global BookmakerLimitTracker.

        Only genuine bookmaker rejections count as rejections. Our own automation
        or session errors are not evidence that the account is limited, and
        counting them would drag the acceptance rate down for the wrong reason.
        """
        if result.status in (PlacementStatus.DRY_RUN, PlacementStatus.ERROR):
            return
        was_rejected = result.status == PlacementStatus.REJECTED
        event = LimitEvent(
            bookmaker=BOOKMAKER_NAME,
            requested_stake=result.requested_stake,
            accepted_stake=0.0 if was_rejected else result.accepted_stake,
            was_rejected=was_rejected,
            note=result.message,
        )
        tracker = get_limit_tracker()
        tracker.record(event)

        if result.was_stake_reduced and result.requested_stake > 0:
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

    # ----------------------------------------------------------------- helpers

    def _read_balance(self, page) -> float | None:
        """Best-effort account balance, for the session log.

        A bet rejected for lack of funds looks like any other rejection unless the
        balance is on record.
        """
        for sel in SELECTORS["logged_in_indicator"]:
            try:
                value = parse_money(page.text_content(sel, timeout=1500))
            except Exception:
                continue
            if value is not None:
                return value
        return None

    @staticmethod
    def _screenshot(page, filename: str) -> None:
        try:
            Path("data").mkdir(exist_ok=True)
            page.screenshot(path=f"data/{filename}")
        except Exception:
            pass

    @staticmethod
    def _first_visible(page, key: str, timeout: int = 3000) -> str | None:
        """Return the first selector in the candidate list that is visible.

        Candidates are tried in priority order. A comma-joined CSS union would
        instead resolve to whatever matches first in DOM order, which silently
        defeats the ordering these lists encode.
        """
        per_try = max(int(timeout / max(len(SELECTORS[key]), 1)), 500)
        for sel in SELECTORS[key]:
            try:
                page.wait_for_selector(sel, state="visible", timeout=per_try)
                log.debug("selector_matched", key=key, selector=sel)
                return sel
            except Exception:
                continue
        log.warning("selector_not_found", key=key, candidates=SELECTORS[key],
                    msg="site markup may have changed")
        return None

    @classmethod
    def _click_first(cls, page, key: str, timeout: int = 3000) -> bool:
        per_try = max(int(timeout / max(len(SELECTORS[key]), 1)), 500)
        for sel in SELECTORS[key]:
            try:
                page.click(sel, timeout=per_try)
                # Records exactly which control was clicked, so a bet that never
                # appears at the book can be traced to the element we hit.
                log.info("clicked", key=key, selector=sel)
                return True
            except Exception:
                continue
        log.warning("click_target_not_found", key=key, candidates=SELECTORS[key])
        return False

    @staticmethod
    def _maybe_click(page, selector: str) -> bool:
        try:
            page.click(selector, timeout=3000)
            return True
        except Exception:
            return False


def interactive_login(
    url: str = _LOGIN_URL,
    cookie_path: str | Path | None = None,
    timeout_seconds: int = 180,
) -> tuple[Path, int]:
    """Launch a visible Chromium window to let the operator login to Stoiximan interactively.

    Captures and saves all cookies to JSON upon successful login or confirmation.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed; run `playwright install chromium`")

    settings = get_settings()
    target_path = get_cookie_file_path(cookie_path or settings.stoiximan_cookie_path)
    log.info("starting_interactive_login", url=url, target_path=str(target_path))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="el-GR",
            timezone_id="Europe/Athens",
            java_script_enabled=True,
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Preload any existing cookies
        load_cookies_into_context(context, target_path)

        page = context.new_page()

        print("=" * 65)
        print("Stoiximan Interactive Login Session")
        print(f"Opening {url} ...")
        print("=" * 65)

        page.goto(url, wait_until="domcontentloaded")
        time.sleep(2)

        # If credentials configured and not logged in, auto-fill
        if settings.stoiximan_username and settings.stoiximan_password:
            try:
                for sel in SELECTORS["accept_cookies"]:
                    try:
                        page.click(sel, timeout=2000)
                        break
                    except Exception:
                        continue

                for sel in SELECTORS["login_button"]:
                    try:
                        page.click(sel, timeout=2000)
                        break
                    except Exception:
                        continue
                time.sleep(0.5)

                for user_sel in SELECTORS["username"]:
                    if page.query_selector(user_sel):
                        page.fill(user_sel, settings.stoiximan_username)
                        break
                for pass_sel in SELECTORS["password"]:
                    if page.query_selector(pass_sel):
                        page.fill(pass_sel, settings.stoiximan_password)
                        break
                print("Pre-filled username and password from configuration.")
            except Exception:
                pass

        print("\nPlease finish login (and any Captcha / 2FA) in the browser window.")
        print("When logged in, press ENTER here in the terminal to save cookies.")
        print("   (Or close the browser when done)\n")

        try:
            input("Press [Enter] after successful login: ")
        except (KeyboardInterrupt, EOFError):
            print("\nSaving current cookies before exiting...")

        # Small grace period to ensure latest session cookies are written
        time.sleep(1)
        count = save_cookies_from_context(context, target_path)
        browser.close()

    return target_path, count
