#!/usr/bin/env python3
"""Send a daily Telegram summary of pipeline activity.

Schedule once per day via cron or systemd timer:
    0 9 * * * cd /home/pi/predictions-sandbox && .venv/bin/python scripts/run_daily_summary.py
"""
import asyncio
import logging

from src.db.connection import get_db
from src.notifications.telegram import notify_daily_summary
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)


async def send_daily() -> None:
    setup_logging()

    async with get_db() as db:
        # Today's API usage
        async with db.execute(
            "SELECT calls, est_cost_usd FROM api_cost_log WHERE date = date('now')"
        ) as cur:
            cost_row = await cur.fetchone()

        api_calls = cost_row["calls"] if cost_row else 0
        est_cost = cost_row["est_cost_usd"] if cost_row else 0.0

        # Today's predictions
        async with db.execute(
            """SELECT COUNT(*) as cnt FROM predictions
               WHERE timestamp >= datetime('now', 'start of day')"""
        ) as cur:
            pred_row = await cur.fetchone()
        predictions_today = pred_row["cnt"]

        # Recent mean Brier score (last 7 days)
        async with db.execute(
            """SELECT AVG(brier_score) as mean_brier FROM calibration
               WHERE resolved_at >= datetime('now', '-7 days')"""
        ) as cur:
            brier_row = await cur.fetchone()
        mean_brier = brier_row["mean_brier"] if brier_row else None

    await notify_daily_summary(
        predictions_today=predictions_today,
        api_calls=api_calls,
        est_cost_usd=est_cost,
        mean_brier=mean_brier,
    )
    logger.info("Daily summary sent")


if __name__ == "__main__":
    asyncio.run(send_daily())
