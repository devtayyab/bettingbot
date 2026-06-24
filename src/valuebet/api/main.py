"""FastAPI service: signals feed, approval workflow, manual placement, P&L, dashboard."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..config import get_settings
from ..core.models import Sport
from ..db.models import Bet, Signal
from ..db.repository import open_signals, pnl_summary, record_bet
from ..db.session import session_scope
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
    confirm_prob: Optional[float]
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


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "env": s.env, "dry_run": s.placement_dry_run}


@app.post("/scan")
def trigger_scan(sport: str = "soccer", live: bool = False) -> dict:
    try:
        sport_enum = Sport(sport)
    except ValueError as exc:
        raise HTTPException(400, f"unknown sport: {sport}") from exc
    count = run_scan(sport_enum, live=live)
    return {"new_signals": count}


@app.get("/signals", response_model=list[SignalOut])
def list_signals(status: str | None = None) -> list[SignalOut]:
    with session_scope() as session:
        return [_to_out(s) for s in open_signals(session, status)]


@app.post("/signals/{signal_id}/approve")
def approve_signal(signal_id: int) -> dict:
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            raise HTTPException(404, "signal not found")
        if sig.status != "detected":
            raise HTTPException(409, f"signal is '{sig.status}', cannot approve")
        sig.status = "approved"
    return {"id": signal_id, "status": "approved"}


@app.post("/signals/{signal_id}/reject")
def reject_signal(signal_id: int) -> dict:
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            raise HTTPException(404, "signal not found")
        sig.status = "rejected"
    return {"id": signal_id, "status": "rejected"}


@app.post("/signals/{signal_id}/place")
def place_bet(signal_id: int, body: PlaceIn) -> dict:
    """Place the bet for an (approved) signal on Stoiximan.

    Honours PLACEMENT_REQUIRE_APPROVAL and PLACEMENT_DRY_RUN from config. Placement
    runs the Playwright worker; in dry-run it prepares the slip without committing.
    """
    s = get_settings()
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            raise HTTPException(404, "signal not found")
        if s.placement_require_approval and sig.status != "approved":
            raise HTTPException(409, "approval required before placement")
        if sig.status == "placed":
            raise HTTPException(409, "already placed")

        min_odds = round(sig.target_odds * (1 - body.slippage), 2)
        request = PlacementRequest(
            event_id=str(sig.event_id), market_type=sig.market_type,
            selection=sig.selection, stake=sig.recommended_stake, min_odds=min_odds,
        )
        # Lazy import keeps Playwright optional for non-placement deployments.
        from ..placement.stoiximan import StoiximanPlacer

        result = StoiximanPlacer().place(request)
        if not result.success:
            # No bet was struck (price moved, automation error, dry-run abort):
            # leave the signal approved so it can be retried; record nothing.
            return {
                "signal_id": signal_id, "bet_id": None, "success": False,
                "dry_run": result.dry_run, "message": result.message,
                "placed_odds": result.placed_odds,
            }
        bet = record_bet(
            session, sig, placed_odds=result.placed_odds or sig.target_odds,
            stake=result.stake, dry_run=result.dry_run, note=result.message,
        )
        return {
            "signal_id": signal_id, "bet_id": bet.id, "success": result.success,
            "dry_run": result.dry_run, "message": result.message,
            "placed_odds": result.placed_odds,
        }


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
        bet.outcome = body.outcome
        if body.outcome == "won":
            bet.profit = round(bet.stake * (bet.placed_odds - 1), 2)
        elif body.outcome == "lost":
            bet.profit = -bet.stake
        else:
            bet.profit = 0.0
        return {"bet_id": bet_id, "outcome": body.outcome, "profit": bet.profit}


@app.get("/pnl")
def pnl() -> dict:
    with session_scope() as session:
        return pnl_summary(session)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML
