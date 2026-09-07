"""FastAPI service: signals feed, approval workflow, manual placement, P&L, dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ..config import get_settings
from ..core.models import Sport
from ..db.models import Bet, Signal
from ..db.repository import (
    all_bookmaker_limit_summaries,
    bookmaker_limit_summary,
    open_signals,
    pnl_summary,
    record_bet,
    save_limit_event,
)
from ..db.session import get_engine, session_scope
from ..logging import configure_logging, get_logger
from ..pipeline import run_scan
from ..placement.base import PlacementRequest
from .dashboard import DASHBOARD_HTML

configure_logging()
log = get_logger("api")
app = FastAPI(title="ValueBet Pilot", version="0.1.0")


class SignalOut(BaseModel):
    id: int
    event_id: int
    selection: str
    sport: str
    market_type: str
    fair_prob: float
    confirm_prob: float | None
    target_odds: float
    edge: float
    recommended_stake: float
    status: str


class PlaceIn(BaseModel):
    # Reject if live odds drop more than this fraction below detected odds.
    slippage: float = 0.02


def _to_out(s: Signal) -> SignalOut:
    return SignalOut(
        id=s.id, event_id=s.event_id, selection=s.selection, sport=s.sport,
        market_type=s.market_type, fair_prob=s.fair_prob, confirm_prob=s.confirm_prob,
        target_odds=s.target_odds, edge=s.edge, recommended_stake=s.recommended_stake,
        status=s.status,
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


def _db_backend() -> str:
    """Report the database actually in use, so a dev SQLite fallback is visible."""
    try:
        return get_engine().url.get_backend_name()
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}"


@app.get("/health")
def health() -> dict:
    s = get_settings()
    from ..placement.session_store import get_cookie_status
    cookies = get_cookie_status()
    return {
        "status": "ok",
        "env": s.env,
        "dry_run": s.placement_dry_run,
        "require_approval": s.placement_require_approval,
        # Surfaced because a scan that "works" while these are wrong is exactly
        # how the app looks healthy while no bet reaches the bookmaker.
        "demo_fallback": s.allow_demo_fallback,
        "has_session_cookies": cookies.get("has_cookies", False),
        "cookie_age_hours": cookies.get("age_hours"),
        "db_backend": _db_backend(),
    }


@app.on_event("shutdown")
def _close_placers() -> None:
    """Close the shared browser sessions so uvicorn reloads don't leak Chromium."""
    try:
        from ..engine.executor import PlacementRouter

        PlacementRouter.shared().close_all()
    except Exception as exc:  # noqa: BLE001
        log.warning("placer_shutdown_failed", error=str(exc))


@app.post("/scan")
def trigger_scan(sport: str = "all", live: bool = False) -> dict:
    try:
        if sport == "all":
            total = 0
            for sp in Sport:
                try:
                    total += run_scan(sp, live=live)
                except Exception as e:
                    log.warning("scan_failed_for_sport", sport=sp.value, error=str(e))
            return {"new_signals": total}

        try:
            sport_enum = Sport(sport)
        except ValueError:
            # If an unknown sport key is passed, fallback to scanning all sports
            total = 0
            for sp in Sport:
                try:
                    total += run_scan(sp, live=live)
                except Exception as e:
                    log.warning("scan_failed_for_sport", sport=sp.value, error=str(e))
            return {"new_signals": total}

        count = run_scan(sport_enum, live=live)
        return {"new_signals": count}
    except Exception as exc:
        log.error("scan_endpoint_failed", sport=sport, live=live, error=str(exc))
        return {"new_signals": 0, "error": str(exc)}


@app.get("/signals", response_model=list[SignalOut])
def list_signals(status: str | None = None) -> list[SignalOut]:
    with session_scope() as session:
        return [_to_out(s) for s in open_signals(session, status)]


@app.post("/signals/{signal_id}/approve")
def approve_signal(signal_id: int) -> dict:
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            log.warning("approve_signal_not_found", signal_id=signal_id)
            raise HTTPException(404, "signal not found")
        if sig.status == "approved":
            return {"id": signal_id, "status": "approved"}
        if sig.status != "detected":
            log.warning("approve_rejected_bad_state", signal_id=signal_id, status=sig.status)
            raise HTTPException(409, f"signal is '{sig.status}', cannot approve")
        sig.status = "approved"
        # Approval is the gate that lets real money out; keep an audit trail.
        log.info("signal_approved", signal_id=signal_id, selection=sig.selection,
                 target_odds=sig.target_odds, recommended_stake=sig.recommended_stake,
                 edge=round(sig.edge, 4))
    return {"id": signal_id, "status": "approved"}


@app.post("/signals/{signal_id}/reject")
def reject_signal(signal_id: int) -> dict:
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            log.warning("reject_signal_not_found", signal_id=signal_id)
            raise HTTPException(404, "signal not found")
        previous = sig.status
        sig.status = "rejected"
        log.info("signal_rejected", signal_id=signal_id, selection=sig.selection,
                 previous_status=previous)
    return {"id": signal_id, "status": "rejected"}


@app.post("/signals/{signal_id}/place")
def place_bet(signal_id: int, body: PlaceIn) -> dict:
    """Place the bet for an (approved) signal on Stoiximan.

    Honours PLACEMENT_REQUIRE_APPROVAL and PLACEMENT_DRY_RUN from config. Placement
    runs the Playwright worker; in dry-run it prepares the slip without committing.

    The response distinguishes what actually happened at the bookmaker via
    `status`, and `placed_on_platform` is true only when the bet was found in
    Stoiximan's own open-bets list. `success` alone must not be read as "the bet
    exists at the book" — a dry run succeeds without staking anything.
    """
    s = get_settings()
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            raise HTTPException(404, "signal not found")
        if s.placement_require_approval and sig.status != "approved":
            raise HTTPException(409, "approval required before placement")
        if sig.status in ("placed", "unconfirmed"):
            raise HTTPException(409, f"signal is '{sig.status}', refusing to place again")

        min_odds = round(sig.target_odds * (1 - body.slippage), 2)
        log.info(
            "placement_requested",
            signal_id=signal_id,
            selection=sig.selection,
            target_odds=sig.target_odds,
            min_odds=min_odds,
            stake=sig.recommended_stake,
            dry_run=s.placement_dry_run,
        )
        request = PlacementRequest(
            event_id=str(sig.event_id), market_type=sig.market_type,
            selection=sig.selection, target_odds=sig.target_odds,
            stake=sig.recommended_stake, min_odds=min_odds,
        )
        # Lazy import keeps Playwright optional for non-placement deployments.
        # The router owns one long-lived browser session; building a placer per
        # request leaked a Chromium process and re-logged in on every click.
        from ..engine.executor import PlacementRouter
        from ..placement.base import PlacementStatus

        placer = PlacementRouter.shared().get_placer(s.placement_bookmaker)
        if placer is None:
            log.error("no_placer_configured", bookmaker=s.placement_bookmaker)
            raise HTTPException(
                501, f"no placer configured for '{s.placement_bookmaker}'"
            )
        result = placer.place(request)
        log.info(
            "placement_completed",
            signal_id=signal_id,
            selection=sig.selection,
            status=result.status.value,
            placed_on_platform=result.verified_on_platform,
            placed_odds=result.placed_odds,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake,
            stake_reduced=result.was_stake_reduced,
            message=result.message,
        )

        def payload(bet_id: int | None) -> dict:
            return {
                "signal_id": signal_id,
                "bet_id": bet_id,
                "success": result.success,
                "status": result.status.value,
                # The only field that means "this bet exists at the bookmaker".
                "placed_on_platform": result.verified_on_platform,
                "needs_manual_check": result.needs_manual_check,
                "dry_run": result.dry_run,
                "message": result.message,
                "placed_odds": result.placed_odds,
                "requested_stake": result.requested_stake,
                "accepted_stake": result.accepted_stake,
                "stake_reduced": result.was_stake_reduced,
            }

        if result.status == PlacementStatus.DRY_RUN:
            # Nothing was staked. Keep the paper record, but mark the signal
            # "paper" rather than "placed" so the dashboard cannot imply a bet
            # exists at Stoiximan when none does.
            bet = record_bet(
                session, sig,
                placed_odds=result.placed_odds or sig.target_odds,
                stake=result.accepted_stake,
                requested_stake=result.requested_stake,
                dry_run=True, note=result.message,
                bookmaker=result.bookmaker, verified=False,
            )
            return payload(bet.id)

        if not result.is_real_bet:
            # No bet was struck (price moved, automation error): leave the signal
            # approved so it can be retried; record nothing.
            if result.status == PlacementStatus.REJECTED:
                save_limit_event(
                    session, bookmaker=result.bookmaker,
                    requested_stake=result.requested_stake,
                    accepted_stake=0.0, was_rejected=True, note=result.message,
                )
            return payload(None)

        bet = record_bet(
            session, sig,
            placed_odds=result.placed_odds or sig.target_odds,
            stake=result.accepted_stake,              # Feature 3: accepted amount
            requested_stake=result.requested_stake,  # Feature 3: what we asked
            dry_run=False, note=result.message,
            bookmaker=result.bookmaker,
            verified=result.verified_on_platform,
        )
        # Feature 4: also persist limit event to DB
        save_limit_event(
            session,
            bookmaker=result.bookmaker,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake,
            was_rejected=False,
            note=result.message,
        )
        if result.needs_manual_check:
            log.error("placement_unconfirmed", signal_id=signal_id,
                      selection=sig.selection, stake=result.accepted_stake,
                      msg=result.message)
        return payload(bet.id)


class SettleIn(BaseModel):
    outcome: str  # won | lost | void


@app.post("/bets/{bet_id}/settle")
def settle_bet(bet_id: int, body: SettleIn) -> dict:
    if body.outcome not in {"won", "lost", "void"}:
        raise HTTPException(400, "outcome must be won|lost|void")
    with session_scope() as session:
        bet = session.get(Bet, bet_id)
        if not bet:
            raise HTTPException(404, "bet not found")
        previous = bet.outcome
        bet.outcome = body.outcome
        if body.outcome == "won":
            bet.profit = round(bet.stake * (bet.placed_odds - 1), 2)
        elif body.outcome == "lost":
            bet.profit = -bet.stake
        else:
            bet.profit = 0.0
        log.info("bet_settled", bet_id=bet_id, selection=bet.selection,
                 previous_outcome=previous, outcome=body.outcome,
                 stake=bet.stake, placed_odds=bet.placed_odds,
                 profit=bet.profit, dry_run=bet.dry_run)
        return {"bet_id": bet_id, "outcome": body.outcome, "profit": bet.profit}


@app.get("/pnl")
def pnl() -> dict:
    with session_scope() as session:
        return pnl_summary(session)


# ---------------------------------------------------------------------------
# Feature 4: Bookmaker Limit / Account Health endpoints
# ---------------------------------------------------------------------------

@app.get("/limits")
def get_all_limits() -> list[dict]:
    """Return stake-acceptance summary for all bookmakers.

    An acceptance_rate below 0.70 with at least 5 bets indicates the account
    is likely being limited and the operator should investigate.
    """
    with session_scope() as session:
        return all_bookmaker_limit_summaries(session)


@app.get("/limits/{bookmaker}")
def get_bookmaker_limits(bookmaker: str, last_n: int = 50) -> dict:
    """Return stake-acceptance summary for one bookmaker."""
    with session_scope() as session:
        return bookmaker_limit_summary(session, bookmaker, last_n)


@app.get("/cookie-status")
def cookie_status() -> dict:
    """Return whether Stoiximan session cookies are saved and how old they are."""
    from ..placement.session_store import get_cookie_status
    return get_cookie_status()


class CookieImport(BaseModel):
    cookies: list[dict]  # Array of cookie objects from Cookie-Editor / Playwright


@app.post("/import-cookies")
def import_cookies(body: CookieImport) -> dict:
    """Import Stoiximan session cookies exported from the browser.

    Use the 'Cookie-Editor' Chrome/Firefox extension or JSON array:
    1. Log in to stoiximan.com.cy manually in your browser.
    2. Click Cookie-Editor extension → Export → Export as JSON.
    3. Paste the JSON array here.
    """
    if not body.cookies:
        raise HTTPException(400, "cookies list is empty")
    from ..placement.session_store import save_raw_cookie_list
    try:
        count = save_raw_cookie_list(body.cookies)
        log.info("cookies_imported", count=count)
        return {
            "success": True,
            "imported": count,
            "message": f"✅ {count} cookies imported and saved to JSON! Bot will reuse this session.",
        }
    except Exception as exc:
        raise HTTPException(400, f"Failed to save cookies: {exc}")


@app.get("/export-cookies")
def export_cookies() -> list[dict]:
    """Export current Stoiximan session cookies as JSON."""
    from ..placement.session_store import read_cookie_json
    return read_cookie_json()


@app.delete("/import-cookies")
def clear_cookies() -> dict:
    """Clear saved Stoiximan cookies (forces re-login on next placement)."""
    from ..placement.session_store import delete_cookie_file
    deleted = delete_cookie_file()
    log.warning("cookies_cleared", deleted=deleted,
                msg="placement will re-login on the next attempt")
    if deleted:
        return {"success": True, "message": "Cookies cleared from JSON file."}
    return {"success": True, "message": "No cookie file found to delete."}


@app.get("/debug/screenshot")
def debug_screenshot():
    """View the latest diagnostic screenshot from Stoiximan browser automation."""
    from fastapi.responses import FileResponse
    for p in [Path("data/navigate_failed.png"), Path("data/stoiximan_blocked.png")]:
        if p.exists():
            return FileResponse(p, media_type="image/png")
    raise HTTPException(404, "No screenshot available yet")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML
