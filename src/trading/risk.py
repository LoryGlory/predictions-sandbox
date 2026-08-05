"""Budget guardian and kill switch for the prediction pipeline.

Enforces hard spending limits so no accidental overspending occurs.
Calibration mode = daily_limit of 0, which blocks all trades (paper included).

The pipeline is a short-lived process-per-cycle cron job, so in-memory
counters reset every 30 minutes. Real protection requires seeding state from
the trades table (the source of truth) at the start of every cycle:

    guardian = BudgetGuardian.from_settings()
    await guardian.seed_from_db(db)          # loads live spend/exposure/losses
    guardian.check_and_record(bet_size=5.0)  # raises if a limit is exceeded

Semantics (all amounts in mana for Manifold live trading):
    daily_limit    — max live spend per rolling 24h window
    total_limit    — max OPEN live exposure (unsettled positions), and also
                     the bankroll figure used for Kelly sizing
    kill switch    — trips when NET realized live losses reach
                     kill_switch_loss_pct * bankroll; halts live trading
                     until an operator raises the limits or resets
"""
from config.settings import settings


class BudgetExceededError(Exception):
    """Raised when a bet would exceed a configured spending limit."""


class KillSwitchError(Exception):
    """Raised when cumulative losses exceed the kill switch threshold.

    When triggered, all live trading halts until manual intervention.
    """


class BudgetGuardian:
    """Tracks live spending and enforces hard limits.

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
        self.open_exposure: float = 0.0
        self.total_losses: float = 0.0
        self._kill_switch_active: bool = False

    @classmethod
    def from_settings(cls) -> "BudgetGuardian":
        """Create a guardian from the central settings object."""
        return cls(
            daily_limit=settings.budget_daily_limit,
            total_limit=settings.budget_total_limit,
            bankroll=settings.budget_total_limit,
            kill_switch_loss_pct=settings.kill_switch_loss_pct,
        )

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def seed(
        self,
        daily_spent: float,
        open_exposure: float,
        total_losses: float,
    ) -> None:
        """Load state computed from the trades table and evaluate the kill switch.

        Call once at cycle start. total_losses is NET realized live loss
        (wins offset losses); negative-or-zero means profitable and is
        clamped to 0.
        """
        self.daily_spent = max(0.0, daily_spent)
        self.open_exposure = max(0.0, open_exposure)
        self.total_losses = max(0.0, total_losses)
        if self.total_losses >= self.kill_switch_threshold:
            self._kill_switch_active = True

    async def seed_from_db(self, db) -> None:
        """Seed state from the trades table on an open aiosqlite connection.

        Only LIVE trades (is_paper=0) count — paper trades spend nothing.
        """
        async with db.execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN timestamp > datetime('now','-1 day')
                                     THEN size ELSE 0 END), 0) AS daily_spent,
                   COALESCE(SUM(CASE WHEN pnl IS NULL
                                     THEN size ELSE 0 END), 0) AS open_exposure,
                   COALESCE(-SUM(pnl), 0) AS net_loss
               FROM trades WHERE is_paper = 0"""
        ) as cur:
            row = await cur.fetchone()
        self.seed(
            daily_spent=row["daily_spent"],
            open_exposure=row["open_exposure"],
            total_losses=row["net_loss"],
        )

    def paper_trading_allowed(self) -> bool:
        """Calibration-mode gate: daily_limit=0 blocks paper trades too."""
        return self.daily_limit > 0

    def check_and_record(self, bet_size: float) -> None:
        """Check that a LIVE bet is within all limits, then record it.

        Raises:
            KillSwitchError: If the kill switch has been triggered.
            BudgetExceededError: If daily or open-exposure limits would be breached.
        """
        if self._kill_switch_active:
            raise KillSwitchError(
                f"Kill switch is active (net live losses {self.total_losses:.2f} >= "
                f"threshold {self.kill_switch_threshold:.2f}) — halt all live "
                "trading. Raise BUDGET_TOTAL_LIMIT or investigate before resuming."
            )

        if self.daily_spent + bet_size > self.daily_limit:
            raise BudgetExceededError(
                f"daily limit exceeded: {self.daily_spent + bet_size:.2f} > {self.daily_limit:.2f}"
            )

        if self.open_exposure + bet_size > self.total_limit:
            raise BudgetExceededError(
                f"open exposure limit exceeded: "
                f"{self.open_exposure + bet_size:.2f} > {self.total_limit:.2f}"
            )

        self.daily_spent += bet_size
        self.open_exposure += bet_size

    def record_loss(self, amount: float) -> None:
        """Record a realized loss and trigger the kill switch if threshold is exceeded.

        Kept for completeness; with seed_from_db the resolver's settled losses
        flow in automatically at the next cycle start.
        """
        self.total_losses += amount
        if self.total_losses >= self.kill_switch_threshold:
            self._kill_switch_active = True

    def reset_daily(self) -> None:
        """Reset daily counter — call this at the start of each day."""
        self.daily_spent = 0.0
