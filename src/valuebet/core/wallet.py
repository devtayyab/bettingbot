"""Wallet management module to track real-time bookmaker balances dynamically."""

from __future__ import annotations

from typing import Protocol
from ..logging import get_logger

log = get_logger("core.wallet")

# Set once the mock-wallet warning has been emitted for this process.
_WARNED = False


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
        # Kelly sizing is driven by these numbers, so a hardcoded balance means
        # stakes are computed against a bankroll the account may not have. Warn
        # once per process: a ValueEngine (and so a wallet) is built per sport per
        # scan, which would otherwise repeat this dozens of times a cycle.
        global _WARNED
        if not _WARNED:
            _WARNED = True
            log.warning(
                "mock_wallet_in_use",
                balances=self.balances,
                msg="stake sizing uses hardcoded balances, not real bookmaker funds",
            )

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
