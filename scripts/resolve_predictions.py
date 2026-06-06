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
from src.trading.pnl import compute_pnl

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


async def _outcome_from_calibration(db, market_db_id: int) -> int | None:
    """Read the resolved outcome from existing calibration rows if available.

    Avoids re-hitting the Manifold/Polymarket API for markets we've already
    resolved before — relevant during the trade-pnl backfill pass.
    """
    async with db.execute(
        """SELECT c.actual_outcome
           FROM calibration c
           JOIN predictions p ON c.prediction_id = p.id
           WHERE p.market_id = ? AND c.actual_outcome IS NOT NULL
           LIMIT 1""",
        (market_db_id,),
    ) as cur:
        row = await cur.fetchone()
        return row["actual_outcome"] if row else None


async def _settle_trades_for_market(db, market_db_id: int, outcome: int) -> int:
    """Compute and store P&L for every unsettled trade on a resolved market.

    Returns the number of trades settled this call.
    """
    async with db.execute(
        """SELECT id, direction, size, entry_price FROM trades
           WHERE market_id = ? AND pnl IS NULL""",
        (market_db_id,),
    ) as cur:
        unsettled = [dict(r) for r in await cur.fetchall()]

    if not unsettled:
        return 0

    outcome_str = "YES" if outcome == 1 else "NO"
    settled = 0
    for t in unsettled:
        try:
            pnl = compute_pnl(t["direction"], t["entry_price"], t["size"], outcome)
        except ValueError as e:
            logger.warning("Skipping trade %s — bad data for pnl: %s", t["id"], e)
            continue
        await db.execute(
            "UPDATE trades SET outcome = ?, pnl = ? WHERE id = ?",
            (outcome_str, pnl, t["id"]),
        )
        settled += 1
    return settled


async def _fetch_outcome(
    db,
    market: dict,
    manifold: ManifoldClient,
    poly: PolymarketClient,
) -> int | None:
    """Return YES/NO outcome for a market, preferring DB cache over API."""
    outcome = await _outcome_from_calibration(db, market["id"])
    if outcome is not None:
        return outcome
    if market["platform"] == "manifold":
        return await _check_manifold(manifold, market["external_id"])
    if market["platform"] == "polymarket":
        return await _check_polymarket(poly, market["external_id"])
    return None


async def _insert_missing_calibration(db, market_db_id: int, outcome: int) -> int:
    """Insert calibration rows for any predictions on this market that lack them."""
    async with db.execute(
        """SELECT p.id, p.estimated_prob
           FROM predictions p
           LEFT JOIN calibration c ON c.prediction_id = p.id
           WHERE p.market_id = ? AND c.id IS NULL""",
        (market_db_id,),
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
    return len(preds)


async def _process_market(
    db,
    market: dict,
    manifold: ManifoldClient,
    poly: PolymarketClient,
) -> tuple[bool, int, int]:
    """Resolve + settle one market. Returns (resolved, cal_inserts, trades_settled)."""
    outcome = await _fetch_outcome(db, market, manifold, poly)
    if outcome is None:
        return False, 0, 0

    logger.info(
        "Resolved [%s] %s -> %s",
        market["platform"], market["question"][:60],
        "YES" if outcome == 1 else "NO",
    )
    cal_inserts = await _insert_missing_calibration(db, market["id"], outcome)
    settled = await _settle_trades_for_market(db, market["id"], outcome)
    if settled:
        logger.info(
            "  Settled %d trade(s) on %s", settled, market["question"][:50],
        )
    await db.commit()
    return True, cal_inserts, settled


async def resolve_all() -> None:
    """Backfill calibration rows + trade P&L for resolved markets."""
    setup_logging()

    async with get_db() as db:
        # Pick up markets that either (a) have an unresolved prediction or
        # (b) have an unsettled trade. The second clause backfills P&L for
        # markets where calibration was filled long ago but trades never had
        # pnl computed.
        async with db.execute(
            """SELECT DISTINCT m.id, m.platform, m.external_id, m.question
               FROM markets m
               WHERE EXISTS (
                   SELECT 1 FROM predictions p
                   LEFT JOIN calibration c ON c.prediction_id = p.id
                   WHERE p.market_id = m.id AND c.id IS NULL
               )
               OR EXISTS (
                   SELECT 1 FROM trades t
                   WHERE t.market_id = m.id AND t.pnl IS NULL
               )"""
        ) as cur:
            pending = [dict(r) for r in await cur.fetchall()]

        logger.info(
            "Checking resolution for %d markets with pending predictions or trades",
            len(pending),
        )

        resolved_count = 0
        cal_inserts = 0
        trades_settled = 0

        async with ManifoldClient() as manifold, PolymarketClient() as poly:
            for m in pending:
                ok, cals, settled = await _process_market(db, m, manifold, poly)
                resolved_count += int(ok)
                cal_inserts += cals
                trades_settled += settled

        logger.info(
            "Resolution pass complete: %d markets resolved, "
            "%d calibration rows inserted, %d trades settled",
            resolved_count, cal_inserts, trades_settled,
        )


if __name__ == "__main__":
    asyncio.run(resolve_all())
