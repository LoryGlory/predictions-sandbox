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
from src.content.story_collector import check_big_edge, check_cost_milestone
from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.markets.polymarket import PolymarketClient
from src.markets.scanner import filter_markets, filter_polymarket_markets
from src.notifications.telegram import notify_error
from src.tracking.logger import setup_logging
from src.trading.executor import TradeExecutor
from src.trading.kelly import kelly_bet_size
from src.trading.risk import BudgetGuardian

logger = logging.getLogger(__name__)

COST_PER_ESTIMATE_USD = 0.003


async def _run_polymarket_cycle(db, estimator, executor, guardian) -> None:
    """Run Polymarket paper trading — same estimator and budget as Manifold."""
    logger.info("Starting Polymarket paper trading cycle")

    try:
        async with PolymarketClient() as poly:
            raw_markets = await poly.get_markets(limit=settings.max_markets_per_cycle * 3)
    except Exception as e:
        logger.error("Polymarket fetch failed: %s", e)
        return

    candidates = filter_polymarket_markets(raw_markets, limit=settings.max_markets_per_cycle)
    logger.info("Polymarket: %d tradeable markets", len(candidates))

    for market in candidates:
        question = market.get("question", "")
        # Polymarket returns outcomePrices as JSON string e.g. '["0.535", "0.465"]'
        outcome_prices = market.get("outcomePrices", [0.5, 0.5])
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = [0.5, 0.5]
        market_price = float(outcome_prices[0]) if outcome_prices else 0.5
        # Set probability so the executor can read it (Polymarket doesn't have this field)
        market["probability"] = market_price
        external_id = str(market.get("id", market.get("condition_id", "")))
        tags = market.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        tags_json = json.dumps(tags) if tags else None

        # Upsert market record
        await db.execute(
            """INSERT INTO markets (platform, external_id, question, current_price, tags)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(platform, external_id) DO UPDATE SET
                   current_price=excluded.current_price,
                   tags=excluded.tags,
                   last_updated=datetime('now')""",
            ("polymarket", external_id, question, market_price, tags_json),
        )
        await db.commit()

        async with db.execute(
            "SELECT id FROM markets WHERE platform=? AND external_id=?",
            ("polymarket", external_id),
        ) as cur:
            row = await cur.fetchone()
        market_db_id = row["id"]

        # Dedup check
        async with db.execute(
            """SELECT market_price, timestamp FROM predictions
               WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1""",
            (market_db_id,),
        ) as cur:
            last_pred = await cur.fetchone()

        if last_pred and last_pred["market_price"] is not None:
            last_ts = datetime.fromisoformat(last_pred["timestamp"])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            hours_since = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
            price_moved = abs(market_price - last_pred["market_price"])
            if hours_since < 4 and price_moved < 0.03:
                continue

        # API budget check
        async with db.execute(
            "SELECT est_cost_usd FROM api_cost_log WHERE date = date('now')",
        ) as cur:
            cost_row = await cur.fetchone()
        if (cost_row and cost_row["est_cost_usd"] or 0) >= settings.daily_api_budget:
            logger.warning("Daily API budget exhausted — skipping Polymarket")
            break

        category = tags[0] if tags else None
        try:
            estimate = await estimator.estimate(
                question, market_price=market_price, category=category,
            )
        except Exception as e:
            logger.error("Polymarket estimation failed for %s: %s", question[:60], e)
            continue

        # Log prediction
        await db.execute(
            """INSERT INTO predictions
               (market_id, model, estimated_prob, market_price, confidence, reasoning, prompt_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                market_db_id, estimate.model, estimate.estimated_probability,
                market_price, estimate.confidence, estimate.reasoning,
                estimate.prompt_version,
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

        # Compute edge and trade
        edge = estimate.estimated_probability - market_price
        if abs(edge) < settings.min_edge_threshold:
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

        # Use ask price for realistic execution simulation
        trade = await executor.execute(
            market=market,
            direction=direction,
            bet_size=bet,
            prediction_id=prediction_db_id,
            platform="polymarket",
        )

        if trade:
            await db.execute(
                """INSERT INTO trades
                   (market_id, prediction_id, direction, size, entry_price, is_paper)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    market_db_id, prediction_db_id,
                    trade["direction"], trade["size"], trade["entry_price"],
                    trade["is_paper"],
                ),
            )
            await db.commit()


async def run_cycle() -> None:
    setup_logging()
    logger.info("Starting pipeline cycle")

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
                    """SELECT market_price, timestamp FROM predictions
                       WHERE market_id = ?
                       ORDER BY timestamp DESC LIMIT 1""",
                    (market_db_id,),
                ) as cur:
                    last_pred = await cur.fetchone()

                if last_pred and last_pred["market_price"] is not None:
                    last_ts = datetime.fromisoformat(last_pred["timestamp"])
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    hours_since = (
                        datetime.now(timezone.utc) - last_ts
                    ).total_seconds() / 3600
                    price_moved = abs(market_price - last_pred["market_price"])
                    if hours_since < 4 and price_moved < 0.03:
                        logger.info(
                            "Skipping %s — estimated %.1fh ago, price moved %.1f%%",
                            question[:40], hours_since, price_moved * 100,
                        )
                        continue

                # Check daily API budget before calling Claude
                async with db.execute(
                    "SELECT est_cost_usd FROM api_cost_log WHERE date = date('now')",
                ) as cur:
                    cost_row = await cur.fetchone()
                daily_cost = cost_row["est_cost_usd"] if cost_row else 0.0
                if daily_cost >= settings.daily_api_budget:
                    logger.warning(
                        "Daily API budget exhausted ($%.2f / $%.2f) — skipping remaining markets",
                        daily_cost, settings.daily_api_budget,
                    )
                    break
                if daily_cost >= settings.daily_api_budget * 0.8:
                    logger.warning(
                        "Approaching daily API budget: $%.2f / $%.2f",
                        daily_cost, settings.daily_api_budget,
                    )

                # Extract category for prompt hints
                category = (raw_tags[0] if raw_tags else None)

                # Get Claude's estimate
                try:
                    estimate = await estimator.estimate(
                        question, market_price=market_price, category=category,
                    )
                except Exception as e:
                    logger.error("Estimation failed for %s: %s", question[:60], e)
                    continue

                # Log prediction
                await db.execute(
                    """INSERT INTO predictions
                       (market_id, model, estimated_prob, market_price, confidence, reasoning, prompt_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        market_db_id,
                        estimate.model,
                        estimate.estimated_probability,
                        market_price,
                        estimate.confidence,
                        estimate.reasoning,
                        estimate.prompt_version,
                    ),
                )
                await db.commit()

                # A/B test: run both prompt versions on ~10% of markets
                if estimator.should_ab_test():
                    alt_version = (
                        "v1_baseline" if estimate.prompt_version == "v2_market_aware"
                        else "v2_market_aware"
                    )
                    try:
                        alt_estimate = await estimator.estimate(
                            question, market_price=market_price,
                            category=category, prompt_version=alt_version,
                        )
                        await db.execute(
                            """INSERT INTO predictions
                               (market_id, model, estimated_prob, market_price, confidence, reasoning, prompt_version)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (
                                market_db_id,
                                alt_estimate.model,
                                alt_estimate.estimated_probability,
                                market_price,
                                alt_estimate.confidence,
                                alt_estimate.reasoning,
                                alt_estimate.prompt_version,
                            ),
                        )
                        await db.commit()
                        logger.info("A/B test: %s on '%s'", alt_version, question[:40])
                    except Exception as e:
                        logger.warning("A/B test estimate failed: %s", e)

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

                # Capture stories for blog content
                await check_big_edge(db, {
                    "estimated_prob": estimate.estimated_probability,
                    "market_price": market_price,
                    "reasoning": estimate.reasoning,
                    "prompt_version": estimate.prompt_version,
                }, {"question": question})
                await check_cost_milestone(db)

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

        # ── Polymarket paper trading ──────────────────────────────────────
        if settings.polymarket_enabled:
            async with get_db() as poly_db:
                await _run_polymarket_cycle(poly_db, estimator, executor, guardian)

        logger.info("Cycle complete")

    except Exception as e:
        logger.exception("Pipeline cycle failed")
        await notify_error(str(e), context="run_cycle")


if __name__ == "__main__":
    asyncio.run(run_cycle())
