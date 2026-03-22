"""Budget guardian and kill switch for the prediction pipeline.

Enforces hard spending limits so no accidental overspending occurs.
Calibration mode = daily_limit of 0, which blocks all real bets.

Usage:
    guardian = BudgetGuardian.from_settings()
    guardian.check_and_record(bet_size=42.0)  # raises if limit exceeded
"""
from config.settings import settings


class BudgetExceededError(Exception):
    """Raised when a bet would exceed a configured spending limit."""


class KillSwitchError(Exception):
    """Raised when cumulative losses exceed the kill switch threshold.

    When triggered, all trading halts until manual intervention.
    """


class BudgetGuardian:
    """Tracks spending and enforces hard limits.

    All amounts are in the same unit (USD, mana, etc.) depending on platform.
    """

    def __init__(
        self,
        daily_limit: float,
        total_limit: float,
        bankroll: float,
        kill_switch_loss_pct: float = 0.10,
    ) -> None:
        self.daily_limit = daily_limit
        self.total_limit = total_limit
        self.bankroll = bankroll
        self.kill_switch_threshold = bankroll * kill_switch_loss_pct

        self.daily_spent: float = 0.0
        self.total_spent: float = 0.0
        self.total_losses: float = 0.0
        self._kill_switch_active: bool = False

    @classmethod
    def from_settings(cls) -> "BudgetGuardian":
        """Create a guardian from the central settings object."""
        return cls(
            daily_limit=settings.budget_daily_limit,
            total_limit=settings.budget_total_limit,
            bankroll=settings.budget_total_limit,
            kill_switch_loss_pct=0.10,
        )

    def check_and_record(self, bet_size: float) -> None:
        """Check that bet_size is within all limits, then record it.

        Raises:
            KillSwitchError: If the kill switch has been triggered.
            BudgetExceededError: If daily or total limits would be breached.
        """
        if self._kill_switch_active:
            raise KillSwitchError(
                "Kill switch is active — halt all trading. Investigate losses before resuming."
            )

        if self.daily_spent + bet_size > self.daily_limit:
            raise BudgetExceededError(
                f"daily limit exceeded: {self.daily_spent + bet_size:.2f} > {self.daily_limit:.2f}"
            )

        if self.total_spent + bet_size > self.total_limit:
            raise BudgetExceededError(
                f"total limit exceeded: {self.total_spent + bet_size:.2f} > {self.total_limit:.2f}"
            )

        self.daily_spent += bet_size
        self.total_spent += bet_size

    def record_loss(self, amount: float) -> None:
        """Record a realized loss and trigger the kill switch if threshold is exceeded."""
        self.total_losses += amount
        if self.total_losses >= self.kill_switch_threshold:
            self._kill_switch_active = True

    def reset_daily(self) -> None:
        """Reset daily counter — call this at the start of each day."""
        self.daily_spent = 0.0
