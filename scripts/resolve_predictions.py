#!/usr/bin/env python3
"""Resolve live predictions by polling Manifold and Polymarket for market outcomes.

For every prediction without a calibration row, check if its market has resolved.
If so, compute Brier score and insert a calibration row.

Schedule via cron (e.g. every 6 hours):
    0 */6 * * * cd /home/laura/predictions-sandbox && .venv/bin/python scripts/resolve_predictions.py
"""
import asyncio
import json
import logging

from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.markets.polymarket import PolymarketClient
from src.tracking.calibration import brier_score
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)


async def _check_manifold(client: ManifoldClient, external_id: str) -> int | None:
    """Return 1/0 if resolved YES/NO, else None."""
    try:
        market = await client.get_market(external_id)
    except Exception as e:
        logger.debug("Manifold fetch failed for %s: %s", external_id, e)
        return None
    if not market.get("isResolved"):
        return None
    resolution = market.get("resolution")
    if resolution == "YES":
        return 1
    if resolution == "NO":
        return 0
    return None  # MKT, CANCEL, or other — skip


async def _check_polymarket(client: PolymarketClient, external_id: str) -> int | None:
    """Return 1/0 if resolved YES/NO, else None."""
    try:
        market = await client.get_market(external_id)
    except Exception as e:
        logger.debug("Polymarket fetch failed for %s: %s", external_id, e)
        return None
    if not market.get("closed") and market.get("resolved") is not True:
        return None
    # After resolution, outcomePrices is ["1","0"] for YES or ["0","1"] for NO
    outcome_prices = market.get("outcomePrices")
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except (json.JSONDecodeError, TypeError):
            return None
    if not outcome_prices or len(outcome_prices) < 2:
        return None
    try:
        yes_price = float(outcome_prices[0])
    except (ValueError, TypeError):
        return None
    if yes_price >= 0.99:
        return 1
    if yes_price <= 0.01:
        return 0
    return None  # still trading or ambiguous


async def resolve_all() -> None:
    """Backfill calibration rows for any resolved markets with unresolved predictions."""
    setup_logging()

    async with get_db() as db:
        async with db.execute(
            """SELECT DISTINCT m.id, m.platform, m.external_id, m.question
               FROM markets m
               JOIN predictions p ON p.market_id = m.id
               LEFT JOIN calibration c ON c.prediction_id = p.id
               WHERE c.id IS NULL"""
        ) as cur:
            pending = [dict(r) for r in await cur.fetchall()]

        logger.info("Checking resolution for %d markets with pending predictions", len(pending))

        resolved_count = 0
        cal_inserts = 0

        async with ManifoldClient() as manifold, PolymarketClient() as poly:
            for m in pending:
                if m["platform"] == "manifold":
                    outcome = await _check_manifold(manifold, m["external_id"])
                elif m["platform"] == "polymarket":
                    outcome = await _check_polymarket(poly, m["external_id"])
                else:
                    continue

                if outcome is None:
                    continue

                resolved_count += 1
                logger.info(
                    "Resolved [%s] %s -> %s",
                    m["platform"], m["question"][:60], "YES" if outcome == 1 else "NO",
                )

                # Insert calibration for every unresolved prediction on this market
                async with db.execute(
                    """SELECT p.id, p.estimated_prob
                       FROM predictions p
                       LEFT JOIN calibration c ON c.prediction_id = p.id
                       WHERE p.market_id = ? AND c.id IS NULL""",
                    (m["id"],),
                ) as cur:
                    preds = [dict(r) for r in await cur.fetchall()]

                for pred in preds:
                    brier = brier_score(pred["estimated_prob"], outcome)
                    await db.execute(
                        """INSERT INTO calibration
                           (prediction_id, predicted_prob, actual_outcome, brier_score, resolved_at)
                           VALUES (?, ?, ?, ?, datetime('now'))""",
                        (pred["id"], pred["estimated_prob"], outcome, brier),
                    )
                    cal_inserts += 1
                await db.commit()

        logger.info(
            "Resolution pass complete: %d markets resolved, %d calibration rows inserted",
            resolved_count, cal_inserts,
        )


if __name__ == "__main__":
    asyncio.run(resolve_all())
