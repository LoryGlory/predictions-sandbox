"""Trade executor — places bets or logs paper trades.

In calibration mode (daily_limit=0) all trades are paper-only.
In live mode, calls the Manifold API to place real bets.
"""
import logging
from typing import Any

from src.trading.risk import BudgetExceededError, BudgetGuardian, KillSwitchError

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Executes or simulates trades based on Kelly-sized bets.

    Args:
        guardian: Budget guardian instance that enforces spending limits.
        paper_mode: If True, log trades without calling any API.
    """

    def __init__(self, guardian: BudgetGuardian, paper_mode: bool = True) -> None:
        self.guardian = guardian
        self.paper_mode = paper_mode

    async def execute(
        self,
        market: dict[str, Any],
        direction: str,
        bet_size: float,
        prediction_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Attempt to execute a trade.

        Returns a trade record dict (suitable for DB insertion) or None if blocked.
        """
        if bet_size <= 0:
            logger.debug("Skipping trade — bet size is zero")
            return None

        try:
            self.guardian.check_and_record(bet_size)
        except (BudgetExceededError, KillSwitchError) as e:
            logger.warning("Trade blocked: %s", e)
            return None

        trade = {
            "market_id": market.get("id"),
            "prediction_id": prediction_id,
            "direction": direction,
            "size": bet_size,
            "entry_price": market.get("probability", 0.5),
            "is_paper": int(self.paper_mode),
        }

        if self.paper_mode:
            logger.info(
                "PAPER TRADE: %s %s M$%.2f on %s",
                direction,
                market.get("question", "?")[:60],
                bet_size,
                market.get("id"),
            )
        else:
            logger.info("LIVE TRADE: placing bet via Manifold API")
            # TODO: call ManifoldClient.place_bet() when live trading is enabled
            raise NotImplementedError("Live trading not yet implemented — set paper_mode=True")

        return trade
