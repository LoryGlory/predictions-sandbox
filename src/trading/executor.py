"""Trade executor — places bets or logs paper trades.

In calibration mode (daily_limit=0) all trades are paper-only.
In live mode, calls the Manifold API to place real bets.
"""
import logging
from typing import Any

from src.trading.risk import BudgetExceededError, BudgetGuardian, KillSwitchError

logger = logging.getLogger(__name__)

# Simulated costs for Polymarket paper trading
POLYMARKET_GAS_FEE_USD = 0.03  # Average Polygon gas cost per trade


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
        platform: str = "manifold",
        spread: float | None = None,
    ) -> dict[str, Any] | None:
        """Attempt to execute a trade.

        Args:
            market: Market dict from scanner.
            direction: 'yes' or 'no'.
            bet_size: Dollar/mana amount to bet.
            prediction_id: FK to predictions table.
            platform: 'manifold' or 'polymarket'.
            spread: Bid/ask spread from CLOB (Polymarket only).

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

        entry_price = market.get("probability", 0.5)

        trade: dict[str, Any] = {
            "market_id": market.get("id"),
            "prediction_id": prediction_id,
            "direction": direction,
            "size": bet_size,
            "entry_price": entry_price,
            "is_paper": int(self.paper_mode),
        }

        # Add Polymarket-specific simulated costs
        if platform == "polymarket":
            spread_cost = (spread or 0.01) * bet_size  # default 1% spread
            gas_fee = POLYMARKET_GAS_FEE_USD
            trade["spread_cost"] = spread_cost
            trade["gas_fee"] = gas_fee
            trade["total_cost"] = spread_cost + gas_fee
            label = "POLYMARKET PAPER"
        else:
            label = "PAPER" if self.paper_mode else "LIVE"

        if self.paper_mode:
            cost_str = ""
            if platform == "polymarket":
                cost_str = f" (spread: ${trade['spread_cost']:.3f}, gas: ${gas_fee:.3f})"
            logger.info(
                "%s TRADE: %s %s $%.2f on %s%s",
                label,
                direction,
                market.get("question", "?")[:60],
                bet_size,
                market.get("id"),
                cost_str,
            )
        else:
            logger.info("LIVE TRADE: placing bet via Manifold API")
            # TODO: call ManifoldClient.place_bet() when live trading is enabled
            raise NotImplementedError("Live trading not yet implemented — set paper_mode=True")

        return trade
