"""Telegram notifier module.

Sends alerts when new value bets are detected or placed.
"""

from __future__ import annotations

import requests

from .config import get_settings
from .core.models import ValueSignal
from .logging import get_logger

log = get_logger("notifier")


class Notifier:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.enabled = bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)
        if self.enabled:
            self.base_url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"

    def _send_message(self, text: str) -> None:
        if not self.enabled:
            return
        
        try:
            payload = {
                "chat_id": self.settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            res = requests.post(self.base_url, json=payload, timeout=5)
            res.raise_for_status()
        except Exception as e:
            log.error("telegram_notify_failed", error=str(e))

    def notify_signal_detected(self, sig: ValueSignal) -> None:
        if not self.enabled:
            return

        edge_pct = round(sig.edge * 100, 2)
        text = (
            f"🚨 <b>Value Detected!</b>\n\n"
            f"<b>Match:</b> {sig.event_id}\n"
            f"<b>Selection:</b> {sig.selection} ({sig.market_type})\n"
            f"<b>Odds:</b> {sig.target_odds}\n"
            f"<b>Edge:</b> {edge_pct}%\n"
            f"<b>Rec. Stake:</b> €{sig.recommended_stake:.2f}"
        )
        self._send_message(text)

    def notify_bet_placed(self, selection: str, odds: float, stake: float) -> None:
        if not self.enabled:
            return
            
        text = (
            f"✅ <b>Bet Placed!</b>\n\n"
            f"<b>Selection:</b> {selection}\n"
            f"<b>Odds:</b> {odds}\n"
            f"<b>Stake:</b> €{stake:.2f}"
        )
        self._send_message(text)

    def notify_bet_failed(self, selection: str, message: str) -> None:
        if not self.enabled:
            return

        text = (
            f"❌ <b>Placement Failed</b>\n\n"
            f"<b>Selection:</b> {selection}\n"
            f"<b>Reason:</b> {message}"
        )
        self._send_message(text)
