#!/usr/bin/env python3
"""Nightly calibration report — sends a detailed Telegram summary.

Schedule via cron:
    0 2 * * * cd /home/laura/predictions-sandbox && .venv/bin/python scripts/nightly_calibration.py

Calculates Brier scores, skill scores, per-category breakdown, and API spend.
Read-only analysis — does NOT modify prompts or pipeline behavior.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from config.settings import settings
from src.content.story_collector import get_weekly_digest
from src.db.connection import get_db
from src.notifications.telegram import send_message
from src.tracking.calibration import brier_skill_score
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)


async def _query_resolved_today(db) -> list[dict]:
    """Get all predictions that resolved in the last 24 hours."""
    async with db.execute(
        """SELECT
               c.id, c.prediction_id, c.predicted_prob, c.actual_outcome, c.brier_score,
               p.market_price, p.estimated_prob, p.reasoning,
               m.question, m.tags, m.category
           FROM calibration c
           JOIN predictions p ON c.prediction_id = p.id
           JOIN markets m ON p.market_id = m.id
           WHERE c.resolved_at >= datetime('now', '-1 day')
           ORDER BY c.brier_score ASC""",
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _query_api_spend(db) -> tuple[float, float]:
    """Return (today's spend, this month's spend)."""
    async with db.execute(
        "SELECT est_cost_usd FROM api_cost_log WHERE date = date('now')"
    ) as cur:
        row = await cur.fetchone()
    day_spend = row["est_cost_usd"] if row else 0.0

    async with db.execute(
        """SELECT SUM(est_cost_usd) as total FROM api_cost_log
           WHERE date >= date('now', 'start of month')"""
    ) as cur:
        row = await cur.fetchone()
    month_spend = row["total"] if row and row["total"] else 0.0

    return day_spend, month_spend


def _per_category_scores(resolved: list[dict]) -> dict[str, dict]:
    """Compute per-category Brier scores and skill scores."""
    categories: dict[str, dict] = {}

    for r in resolved:
        # Parse tags JSON or use category field
        tags = []
        if r.get("tags"):
            try:
                tags = json.loads(r["tags"])
            except (json.JSONDecodeError, TypeError):
                pass
        if not tags and r.get("category"):
            tags = [r["category"]]
        if not tags:
            tags = ["uncategorized"]

        for tag in tags:
            if tag not in categories:
                categories[tag] = {"model_briers": [], "market_briers": []}
            categories[tag]["model_briers"].append(r["brier_score"])
            if r["market_price"] is not None and r["actual_outcome"] is not None:
                market_brier = (r["market_price"] - r["actual_outcome"]) ** 2
                categories[tag]["market_briers"].append(market_brier)

    result = {}
    for tag, data in categories.items():
        if not data["model_briers"]:
            continue
        mean_model = sum(data["model_briers"]) / len(data["model_briers"])
        mean_market = (
            sum(data["market_briers"]) / len(data["market_briers"])
            if data["market_briers"]
            else None
        )
        skill = None
        if mean_market and mean_market > 0:
            skill = brier_skill_score(mean_model, mean_market)
        result[tag] = {
            "count": len(data["model_briers"]),
            "mean_brier": mean_model,
            "market_brier": mean_market,
            "skill_score": skill,
        }

    return result


def _format_report(
    resolved: list[dict],
    mean_brier: float,
    market_brier: float,
    skill: float,
    best: dict | None,
    worst: dict | None,
    category_scores: dict[str, dict],
    day_spend: float,
    month_spend: float,
) -> str:
    """Format the Telegram HTML report."""
    lines = [
        "<b>Daily Calibration Report</b>",
        "========================",
        f"Resolved today: {len(resolved)}",
        f"Daily Brier: {mean_brier:.4f}",
        f"Market Brier: {market_brier:.4f}",
        f"Skill Score: {skill:+.2f}",
        "",
    ]

    if best:
        outcome_str = "YES" if best["actual_outcome"] == 1 else "NO"
        lines.append(
            f'Best: "{best["question"][:50]}" — '
            f'est {best["estimated_prob"]:.0%}, actual {outcome_str}, '
            f'Brier {best["brier_score"]:.2f}'
        )

    if worst:
        outcome_str = "YES" if worst["actual_outcome"] == 1 else "NO"
        mkt_str = f" (mkt {worst['market_price']:.0%})" if worst["market_price"] else ""
        lines.append(
            f'Worst: "{worst["question"][:50]}" — '
            f'est {worst["estimated_prob"]:.0%}, actual {outcome_str}{mkt_str}, '
            f'Brier {worst["brier_score"]:.2f}'
        )

    # Per-category winners and losers (by skill score)
    scored = {
        k: v for k, v in category_scores.items()
        if v["skill_score"] is not None and v["count"] >= 3
    }
    if scored:
        sorted_cats = sorted(scored.items(), key=lambda x: x[1]["skill_score"], reverse=True)
        winners = sorted_cats[:3]
        losers = sorted_cats[-3:]

        lines.append("")
        if winners and winners[0][1]["skill_score"] > 0:
            winner_strs = [f"{k} ({v['skill_score']:+.2f})" for k, v in winners if v["skill_score"] > 0]
            if winner_strs:
                lines.append(f"Category winners: {', '.join(winner_strs)}")
        if losers and losers[-1][1]["skill_score"] < 0:
            loser_strs = [f"{k} ({v['skill_score']:+.2f})" for k, v in losers if v["skill_score"] < 0]
            if loser_strs:
                lines.append(f"Category losers: {', '.join(loser_strs)}")

    lines.append("")
    lines.append(f"API spend today: ${day_spend:.2f}")
    lines.append(f"API spend this month: ${month_spend:.2f}")

    return "\n".join(lines)


async def run_nightly_report() -> None:
    """Generate and send the nightly calibration report."""
    setup_logging()

    if not settings.nightly_report_enabled:
        logger.info("Nightly report disabled, skipping")
        return

    async with get_db() as db:
        resolved = await _query_resolved_today(db)

        if not resolved:
            logger.info("No resolved predictions in last 24h — skipping report")
            return

        # Compute aggregate scores
        model_briers = [r["brier_score"] for r in resolved if r["brier_score"] is not None]
        market_briers = [
            (r["market_price"] - r["actual_outcome"]) ** 2
            for r in resolved
            if r["market_price"] is not None and r["actual_outcome"] is not None
        ]

        if not model_briers:
            logger.info("No scored predictions — skipping report")
            return

        mean_brier = sum(model_briers) / len(model_briers)
        market_brier = sum(market_briers) / len(market_briers) if market_briers else mean_brier
        skill = brier_skill_score(mean_brier, market_brier) if market_brier > 0 else 0.0

        # Best and worst
        scored_resolved = [r for r in resolved if r["brier_score"] is not None]
        best = scored_resolved[0] if scored_resolved else None  # sorted ASC
        worst = scored_resolved[-1] if scored_resolved else None

        # Per-category
        category_scores = _per_category_scores(resolved)

        # API spend
        day_spend, month_spend = await _query_api_spend(db)

        # Format and send
        report_text = _format_report(
            resolved, mean_brier, market_brier, skill,
            best, worst, category_scores, day_spend, month_spend,
        )

        await send_message(report_text)
        logger.info("Nightly report sent (%d resolved predictions)", len(resolved))

        # Store in daily_reports table
        report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        full_report = json.dumps({
            "resolved_count": len(resolved),
            "mean_brier": mean_brier,
            "market_brier": market_brier,
            "skill_score": skill,
            "category_scores": category_scores,
            "day_spend": day_spend,
            "month_spend": month_spend,
        })

        await db.execute(
            """INSERT INTO daily_reports
               (report_date, resolved_count, mean_brier, market_brier, skill_score,
                best_prediction_id, worst_prediction_id, api_spend_day, api_spend_month, full_report)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(report_date) DO UPDATE SET
                   resolved_count=excluded.resolved_count,
                   mean_brier=excluded.mean_brier,
                   market_brier=excluded.market_brier,
                   skill_score=excluded.skill_score,
                   best_prediction_id=excluded.best_prediction_id,
                   worst_prediction_id=excluded.worst_prediction_id,
                   api_spend_day=excluded.api_spend_day,
                   api_spend_month=excluded.api_spend_month,
                   full_report=excluded.full_report""",
            (
                report_date, len(resolved), mean_brier, market_brier, skill,
                best["prediction_id"] if best else None,
                worst["prediction_id"] if worst else None,
                day_spend, month_spend, full_report,
            ),
        )
        await db.commit()

        # Weekly story digest — Sundays only
        if datetime.now(timezone.utc).weekday() == 6:
            digest = await get_weekly_digest(db)
            if digest:
                digest_lines = [
                    "",
                    "<b>Weekly Story Digest</b>",
                    "======================",
                    f"New stories this week: {digest['total']}",
                ]
                for stype, count in sorted(digest["counts"].items()):
                    label = stype.replace("_", " ")
                    digest_lines.append(f"  - {count} {label}")
                digest_lines.append(
                    "\nRun: python scripts/export_stories.py --format markdown"
                )
                await send_message("\n".join(digest_lines))


if __name__ == "__main__":
    asyncio.run(run_nightly_report())
