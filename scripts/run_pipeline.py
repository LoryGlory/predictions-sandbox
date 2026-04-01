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
import json
import logging
from datetime import datetime, timezone

from config.settings import settings
from src.analysis.estimator import Estimator
from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.markets.scanner import filter_markets
from src.notifications.telegram import notify_cycle_summary, notify_error
from src.tracking.logger import setup_logging
from src.trading.executor import TradeExecutor
from src.trading.kelly import kelly_bet_size
from src.trading.risk import BudgetGuardian

logger = logging.getLogger(__name__)

COST_PER_ESTIMATE_USD = 0.003


async def run_cycle() -> None:
    setup_logging()
    logger.info("Starting pipeline cycle")

    estimates_made = 0
    trades_made = 0
    high_edge_markets: list[dict] = []

    try:
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

                # Extract tags from Manifold market (may be 'groupSlugs', 'tags', or 'groups')
                raw_tags = market.get("groupSlugs") or market.get("tags") or []
                tags_json = json.dumps(raw_tags) if raw_tags else None

                # Upsert market record
                await db.execute(
                    """INSERT INTO markets (platform, external_id, question, current_price, tags)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(platform, external_id) DO UPDATE SET
                           current_price=excluded.current_price,
                           tags=excluded.tags,
                           last_updated=datetime('now')""",
                    ("manifold", external_id, question, market_price, tags_json),
                )
                await db.commit()

                async with db.execute(
                    "SELECT id FROM markets WHERE platform=? AND external_id=?",
                    ("manifold", external_id),
                ) as cur:
                    row = await cur.fetchone()
                market_db_id = row["id"]

                # Check if we estimated this market recently and price hasn't moved much
                async with db.execute(
                    """SELECT estimated_prob, timestamp FROM predictions
                       WHERE market_id = ?
                       ORDER BY timestamp DESC LIMIT 1""",
                    (market_db_id,),
                ) as cur:
                    last_pred = await cur.fetchone()

                if last_pred:
                    hours_since = (
                        datetime.now(timezone.utc) -
                        datetime.fromisoformat(last_pred["timestamp"].replace("Z", "+00:00"))
                    ).total_seconds() / 3600
                    price_moved = abs(market_price - last_pred["estimated_prob"])
                    if hours_since < 4 and price_moved < 0.03:
                        logger.debug(
                            "Skipping %s — estimated %.1fh ago, price moved %.1f%%",
                            question[:40], hours_since, price_moved * 100,
                        )
                        continue

                # Get Claude's estimate
                try:
                    estimate = await estimator.estimate(question, market_price=market_price)
                except Exception as e:
                    logger.error("Estimation failed for %s: %s", question[:60], e)
                    continue

                estimates_made += 1

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

                # Track API cost
                await db.execute(
                    """INSERT INTO api_cost_log (date, calls, est_cost_usd)
                       VALUES (date('now'), 1, ?)
                       ON CONFLICT(date) DO UPDATE SET
                           calls = calls + 1,
                           est_cost_usd = est_cost_usd + excluded.est_cost_usd""",
                    (COST_PER_ESTIMATE_USD,),
                )
                await db.commit()

                async with db.execute("SELECT last_insert_rowid() as id") as cur:
                    pred_row = await cur.fetchone()
                prediction_db_id = pred_row["id"]

                # Compute bet size
                edge = estimate.estimated_probability - market_price
                if abs(edge) >= 0.15:
                    high_edge_markets.append({
                        "question": question,
                        "edge": edge,
                        "estimate": estimate.estimated_probability,
                        "market_price": market_price,
                    })

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
                    trades_made += 1
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

        await notify_cycle_summary(
            markets_scanned=len(candidates),
            estimates_made=estimates_made,
            trades_made=trades_made,
            high_edge_markets=high_edge_markets or None,
        )
        logger.info("Cycle complete")

    except Exception as e:
        logger.exception("Pipeline cycle failed")
        await notify_error(str(e), context="run_cycle")


if __name__ == "__main__":
    asyncio.run(run_cycle())
