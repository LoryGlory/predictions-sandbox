"""Trade executor — places bets or logs paper trades.

Paper mode (default): logs trades to the DB without calling any platform API.
Live mode: calls the Manifold API to place real-mana bets. Live mode is gated
by MANIFOLD_MODE=live in settings, plus four hard safety caps:

  - LIVE_MAX_BET_MANA caps the size of every single bet
  - LIVE_MAX_BETS_PER_CYCLE caps how many live bets one pipeline cycle can place
  - LIVE_MAX_BETS_PER_DAY caps how many live bets across the rolling 24h window
  - LIVE_MAX_DAYS_TO_CLOSE skips live bets on markets that resolve too far out
    (paper trades on those markets still happen for calibration data)

Polymarket bets are always paper (no wallet, no live executor path).
"""
import logging
import time
from typing import Any

from config.settings import settings
from src.markets.manifold import ManifoldClient
from src.trading.risk import BudgetExceededError, BudgetGuardian, KillSwitchError

logger = logging.getLogger(__name__)

# Simulated costs for Polymarket paper trading
POLYMARKET_GAS_FEE_USD = 0.03  # Average Polygon gas cost per trade


class TradeExecutor:
    """Executes or simulates trades based on Kelly-sized bets.

    Args:
        guardian: Budget guardian instance that enforces spending limits.
        paper_mode: If True, log trades without calling any API. Combined with
            settings.manifold_mode == "live", live Manifold trades are placed
            for Manifold markets while Polymarket stays simulated.
        manifold_client: Optional ManifoldClient for live trading. If None and
            live mode is active, will be constructed on demand.
        daily_live_count_provider: Optional callable returning the count of live
            bets placed in the last 24h. Injected to keep the executor stateless
            wrt the database. Returns 0 if None.
    """

    def __init__(
        self,
        guardian: BudgetGuardian,
        paper_mode: bool = True,
        manifold_client: ManifoldClient | None = None,
        daily_live_count_provider: Any = None,
    ) -> None:
        self.guardian = guardian
        self.paper_mode = paper_mode
        self._manifold_client = manifold_client
        self._daily_live_count_provider = daily_live_count_provider
        # Per-cycle counter, reset by the pipeline at the start of each cycle
        self.cycle_live_count = 0

    def reset_cycle_counter(self) -> None:
        """Call at the start of every pipeline cycle to reset the per-cycle cap."""
        self.cycle_live_count = 0

    @staticmethod
    def _within_close_horizon(market: dict[str, Any]) -> bool:
        """True if the market closes within LIVE_MAX_DAYS_TO_CLOSE from now.

        Manifold returns `closeTime` in milliseconds since epoch. Markets
        without a closeTime are treated as outside the horizon (conservative —
        live trading should only happen when we know the resolution timeframe).
        """
        close_time_ms = market.get("closeTime")
        if not close_time_ms:
            return False
        now_ms = time.time() * 1000
        horizon_ms = settings.live_max_days_to_close * 86400 * 1000
        return (close_time_ms - now_ms) <= horizon_ms

    def _should_go_live(self, platform: str, market: dict[str, Any]) -> bool:
        """Returns True if this specific trade should hit the Manifold live API."""
        if self.paper_mode:
            return False
        if platform != "manifold":
            return False
        if settings.manifold_mode.lower() != "live":
            return False
        if not self._within_close_horizon(market):
            close_time_ms = market.get("closeTime")
            if close_time_ms:
                days_out = (close_time_ms - time.time() * 1000) / 86400000
                logger.info(
                    "Live bet skipped on %s — closes in %.0fd (> %d cap)",
                    market.get("id"), days_out, settings.live_max_days_to_close,
                )
            else:
                logger.info(
                    "Live bet skipped on %s — no closeTime in market data",
                    market.get("id"),
                )
            return False
        return True

    async def _check_live_caps(self, bet_size: float) -> tuple[bool, str]:
        """Returns (allowed, reason). reason is empty when allowed."""
        if bet_size > settings.live_max_bet_mana:
            # Note: the caller has already capped bet_size for live; this is a
            # defense-in-depth check in case it didn't.
            return False, f"bet size {bet_size:.0f} exceeds LIVE_MAX_BET_MANA={settings.live_max_bet_mana}"
        if self.cycle_live_count >= settings.live_max_bets_per_cycle:
            return False, f"cycle cap reached: {self.cycle_live_count}/{settings.live_max_bets_per_cycle}"
        if self._daily_live_count_provider is not None:
            try:
                count_today = await self._daily_live_count_provider()
            except Exception as e:
                logger.warning("Daily-count provider failed: %s — blocking live bet", e)
                return False, "daily count unavailable"
            if count_today >= settings.live_max_bets_per_day:
                return False, f"daily cap reached: {count_today}/{settings.live_max_bets_per_day}"
        return True, ""

    @staticmethod
    def _polymarket_costs(bet_size: float, spread: float | None) -> dict[str, float]:
        """Compute simulated Polymarket spread + gas costs for a paper trade."""
        spread_cost = (spread or 0.01) * bet_size
        return {
            "spread_cost": spread_cost,
            "gas_fee": POLYMARKET_GAS_FEE_USD,
            "total_cost": spread_cost + POLYMARKET_GAS_FEE_USD,
        }

    def _log_paper_trade(
        self, label: str, direction: str, question: str,
        bet_size: float, market_id: str, trade: dict[str, Any], platform: str,
    ) -> None:
        cost_str = ""
        if platform == "polymarket":
            cost_str = (
                f" (spread: ${trade['spread_cost']:.3f}, "
                f"gas: ${trade['gas_fee']:.3f})"
            )
        logger.info(
            "%s TRADE: %s %s $%.2f on %s%s",
            label, direction, question[:60], bet_size, market_id, cost_str,
        )

    async def _place_live_bet(
        self, market: dict[str, Any], direction: str, bet_size: float,
        trade: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run the live Manifold bet path; mutates `trade` and returns it (or None)."""
        allowed, reason = await self._check_live_caps(bet_size)
        if not allowed:
            logger.warning("Live bet blocked: %s — falling back to paper trade", reason)
            trade["is_paper"] = 1
            return trade

        amount = int(round(bet_size))
        if amount <= 0:
            logger.info("Live bet rounded to zero — skipping")
            return None

        outcome = direction.upper()
        market_id = market.get("id")
        if not market_id:
            logger.error("Live bet has no market id — skipping")
            return None

        try:
            client = self._manifold_client
            if client is None:
                async with ManifoldClient() as new_client:
                    bet_resp = await new_client.place_bet(market_id, outcome, amount)
            else:
                bet_resp = await client.place_bet(market_id, outcome, amount)
        except Exception:
            logger.exception(
                "Live bet failed for %s — falling back to paper", market_id,
            )
            trade["is_paper"] = 1
            return trade

        self.cycle_live_count += 1
        trade["live_bet_id"] = bet_resp.get("id")
        trade["filled_amount"] = bet_resp.get("amount") or amount
        trade["prob_after"] = bet_resp.get("probAfter")
        logger.info(
            "LIVE TRADE PLACED: %s %s M$%d on %s (filled M$%s, probAfter=%s)",
            outcome, market.get("question", "?")[:50], amount, market_id,
            trade["filled_amount"], trade.get("prob_after"),
        )
        return trade

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

        go_live = self._should_go_live(platform, market)

        # Cap bet size to LIVE_MAX_BET_MANA in live mode before placing
        if go_live and bet_size > settings.live_max_bet_mana:
            logger.info(
                "Capping live bet from M$%.2f to M$%d (LIVE_MAX_BET_MANA)",
                bet_size, settings.live_max_bet_mana,
            )
            bet_size = float(settings.live_max_bet_mana)

        trade: dict[str, Any] = {
            "market_id": market.get("id"),
            "prediction_id": prediction_id,
            "direction": direction,
            "size": bet_size,
            "entry_price": market.get("probability", 0.5),
            "is_paper": 0 if go_live else 1,
        }
        if platform == "polymarket":
            trade.update(self._polymarket_costs(bet_size, spread))

        if not go_live:
            label = (
                "POLYMARKET PAPER" if platform == "polymarket"
                else "PAPER"
            )
            self._log_paper_trade(
                label, direction, market.get("question", "?"),
                bet_size, str(market.get("id")), trade, platform,
            )
            return trade

        return await self._place_live_bet(market, direction, bet_size, trade)
