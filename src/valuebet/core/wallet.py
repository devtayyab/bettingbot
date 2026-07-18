"""Wallet management module to track real-time bookmaker balances dynamically."""

from __future__ import annotations

from typing import Protocol
from ..logging import get_logger

log = get_logger("core.wallet")


class WalletManager(Protocol):
    """Protocol for fetching real-time bankroll balances from bookmakers."""
    
    def get_balance(self, bookmaker: str) -> float:
        """Returns the available balance for fractional Kelly calculation."""
        ...


class MockWalletManager:
    """A dummy wallet manager that starts with a set balance and logs deductions."""
    
    def __init__(self, initial_balances: dict[str, float] = None) -> None:
        self.balances: dict[str, float] = initial_balances or {
            "stoiximan": 500.0,
            "bet365": 500.0,
            "pinnacle": 1000.0
        }

    def get_balance(self, bookmaker: str) -> float:
        balance = self.balances.get(bookmaker, 0.0)
        log.debug("wallet_balance_fetched", bookmaker=bookmaker, balance=balance)
        return balance
        
    def deduct(self, bookmaker: str, amount: float) -> None:
        if bookmaker in self.balances:
            self.balances[bookmaker] -= amount
            log.info("wallet_balance_deducted", bookmaker=bookmaker, amount=amount, new_balance=self.balances[bookmaker])
            
    def credit(self, bookmaker: str, amount: float) -> None:
        if bookmaker in self.balances:
            self.balances[bookmaker] += amount
            log.info("wallet_balance_credited", bookmaker=bookmaker, amount=amount, new_balance=self.balances[bookmaker])
