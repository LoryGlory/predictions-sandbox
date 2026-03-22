#!/usr/bin/env python3
"""Main pipeline entry point — runs one polling cycle.

Schedule this with cron or systemd timer:
    */5 * * * * cd /home/pi/predictions-sandbox && .venv/bin/python scripts/run_pipeline.py

One cycle:
1. Fetch active markets from Manifold
2. Filter to tradeable candidates
3. For each market, ask Claude for a probability estimate
4. Compute Kelly bet size
5. Check budget guardian
6. Execute (paper) trade if edge exists
7. Log everything to SQLite
"""
import asyncio
import logging

from config.settings import settings
from src.analysis.estimator import Estimator
from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.markets.scanner import filter_markets
from src.tracking.logger import setup_logging
from src.trading.executor import TradeExecutor
from src.trading.kelly import kelly_bet_size
from src.trading.risk import BudgetGuardian

logger = logging.getLogger(__name__)


async def run_cycle() -> None:
    setup_logging()
    logger.info("Starting pipeline cycle")

    guardian = BudgetGuardian.from_settings()
    estimator = Estimator()
    executor = TradeExecutor(guardian=guardian, paper_mode=True)

    async with ManifoldClient() as manifold:
        raw_markets = await manifold.get_markets(limit=settings.max_markets_per_cycle * 3)

    candidates = filter_markets(raw_markets, limit=settings.max_markets_per_cycle)
    logger.info("Filtered to %d tradeable markets", len(candidates))

    async with get_db() as db:
        for market in candidates:
            question = market.get("question", "")
            market_price = market.get("probability", 0.5)
            external_id = market.get("id", "")

            # Upsert market record
            await db.execute(
                """INSERT INTO markets (platform, external_id, question, current_price)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET
                       current_price=excluded.current_price,
                       last_updated=datetime('now')""",
                ("manifold", external_id, question, market_price),
            )
            await db.commit()

            async with db.execute(
                "SELECT id FROM markets WHERE platform=? AND external_id=?",
                ("manifold", external_id),
            ) as cur:
                row = await cur.fetchone()
            market_db_id = row["id"]

            # Get Claude's estimate
            try:
                estimate = await estimator.estimate(question, market_price=market_price)
            except Exception as e:
                logger.error("Estimation failed for %s: %s", question[:60], e)
                continue

            # Log prediction
            await db.execute(
                """INSERT INTO predictions (market_id, model, estimated_prob, confidence, reasoning)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    market_db_id,
                    estimate.model,
                    estimate.estimated_probability,
                    estimate.confidence,
                    estimate.reasoning,
                ),
            )
            await db.commit()

            async with db.execute("SELECT last_insert_rowid() as id") as cur:
                pred_row = await cur.fetchone()
            prediction_db_id = pred_row["id"]

            # Compute bet size
            edge = estimate.estimated_probability - market_price
            if abs(edge) < settings.min_edge_threshold:
                logger.debug("No edge on %s (edge=%.3f)", question[:40], edge)
                continue

            direction = "yes" if edge > 0 else "no"
            prob_for_direction = (
                estimate.estimated_probability if direction == "yes"
                else 1 - estimate.estimated_probability
            )
            price_for_direction = market_price if direction == "yes" else 1 - market_price

            bet = kelly_bet_size(
                our_prob=prob_for_direction,
                market_price=price_for_direction,
                bankroll=guardian.total_limit,
                kelly_fraction_multiplier=settings.kelly_fraction,
                max_position_pct=0.05,
            )

            trade = await executor.execute(
                market=market,
                direction=direction,
                bet_size=bet,
                prediction_id=prediction_db_id,
            )

            if trade:
                await db.execute(
                    """INSERT INTO trades
                       (market_id, prediction_id, direction, size, entry_price, is_paper)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        market_db_id,
                        prediction_db_id,
                        trade["direction"],
                        trade["size"],
                        trade["entry_price"],
                        trade["is_paper"],
                    ),
                )
                await db.commit()

    logger.info("Cycle complete")


if __name__ == "__main__":
    asyncio.run(run_cycle())
