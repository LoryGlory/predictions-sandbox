"""Dashboard data access layer — all SQL queries for the read-only dashboard."""
import json

from src.db.connection import get_db
from src.tracking.calibration import brier_skill_score


async def get_overview_stats() -> dict:
    """Aggregate stats for the overview page."""
    async with get_db(read_only=True) as db:
        stats = {}

        async with db.execute("SELECT COUNT(*) as cnt FROM markets") as cur:
            stats["total_markets"] = (await cur.fetchone())["cnt"]

        async with db.execute("SELECT COUNT(*) as cnt FROM predictions") as cur:
            stats["total_predictions"] = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM predictions WHERE date(timestamp) = date('now')"
        ) as cur:
            stats["predictions_today"] = (await cur.fetchone())["cnt"]

        async with db.execute("SELECT COUNT(*) as cnt FROM trades") as cur:
            stats["total_trades"] = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT AVG(brier_score) as avg FROM calibration WHERE actual_outcome IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
            stats["mean_brier"] = row["avg"]

        async with db.execute(
            """SELECT SUM(est_cost_usd) as total FROM api_cost_log
               WHERE date = date('now')"""
        ) as cur:
            row = await cur.fetchone()
            stats["cost_today"] = row["total"] or 0.0

        async with db.execute(
            """SELECT SUM(est_cost_usd) as total FROM api_cost_log"""
        ) as cur:
            row = await cur.fetchone()
            stats["cost_total"] = row["total"] or 0.0

        # Recent predictions
        async with db.execute(
            """SELECT p.estimated_prob, p.confidence, p.timestamp,
                      m.question, m.current_price, m.id as market_id
               FROM predictions p
               JOIN markets m ON p.market_id = m.id
               ORDER BY p.timestamp DESC LIMIT 10"""
        ) as cur:
            stats["recent_predictions"] = [dict(r) for r in await cur.fetchall()]

    return stats


async def get_markets(page: int = 1, per_page: int = 20) -> tuple[list[dict], int]:
    """Paginated markets with prediction counts."""
    offset = (page - 1) * per_page
    async with get_db(read_only=True) as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM markets") as cur:
            total = (await cur.fetchone())["cnt"]

        async with db.execute(
            """SELECT m.*, COUNT(p.id) as prediction_count
               FROM markets m
               LEFT JOIN predictions p ON p.market_id = m.id
               GROUP BY m.id
               ORDER BY m.last_updated DESC
               LIMIT ? OFFSET ?""",
            (per_page, offset),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    return rows, total


async def get_market_detail(market_id: int) -> dict | None:
    """Single market with all predictions, trades, and calibration."""
    async with get_db(read_only=True) as db:
        async with db.execute("SELECT * FROM markets WHERE id = ?", (market_id,)) as cur:
            market_row = await cur.fetchone()

        if not market_row:
            return None

        market = dict(market_row)

        async with db.execute(
            """SELECT p.*, c.actual_outcome, c.brier_score
               FROM predictions p
               LEFT JOIN calibration c ON c.prediction_id = p.id
               WHERE p.market_id = ?
               ORDER BY p.timestamp DESC""",
            (market_id,),
        ) as cur:
            market["predictions"] = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT * FROM trades WHERE market_id = ? ORDER BY timestamp DESC",
            (market_id,),
        ) as cur:
            market["trades"] = [dict(r) for r in await cur.fetchall()]

        # Parse tags
        market["tag_list"] = json.loads(market["tags"]) if market.get("tags") else []

    return market


async def get_calibration_overview() -> dict:
    """Calibration stats: Brier scores, buckets, and category breakdown."""
    result: dict = {}
    async with get_db(read_only=True) as db:
        # Overall stats
        async with db.execute(
            """SELECT COUNT(*) as cnt,
                      AVG(brier_score) as mean_brier
               FROM calibration
               WHERE actual_outcome IS NOT NULL"""
        ) as cur:
            row = await cur.fetchone()
            result["resolved_count"] = row["cnt"]
            result["mean_brier"] = row["mean_brier"]

        # Market baseline Brier
        async with db.execute(
            """SELECT AVG((m.current_price - c.actual_outcome) * (m.current_price - c.actual_outcome))
                      as market_brier
               FROM calibration c
               JOIN predictions p ON c.prediction_id = p.id
               JOIN markets m ON p.market_id = m.id
               WHERE c.actual_outcome IS NOT NULL"""
        ) as cur:
            row = await cur.fetchone()
            result["market_brier"] = row["market_brier"]

        if result["mean_brier"] and result["market_brier"] and result["market_brier"] > 0:
            try:
                result["skill_score"] = brier_skill_score(
                    result["mean_brier"], result["market_brier"]
                )
            except ValueError:
                result["skill_score"] = None
        else:
            result["skill_score"] = None

        # Calibration buckets (deciles)
        async with db.execute(
            """SELECT predicted_prob, actual_outcome
               FROM calibration
               WHERE actual_outcome IS NOT NULL"""
        ) as cur:
            rows = await cur.fetchall()

        buckets: dict[str, dict] = {}
        for r in rows:
            bucket_idx = min(int(r["predicted_prob"] * 10), 9)
            label = f"{bucket_idx * 10}-{(bucket_idx + 1) * 10}%"
            if label not in buckets:
                buckets[label] = {"label": label, "count": 0, "sum_pred": 0.0, "sum_outcome": 0}
            buckets[label]["count"] += 1
            buckets[label]["sum_pred"] += r["predicted_prob"]
            buckets[label]["sum_outcome"] += r["actual_outcome"]

        bucket_list = []
        for label in sorted(buckets.keys()):
            b = buckets[label]
            bucket_list.append({
                "label": label,
                "count": b["count"],
                "mean_predicted": b["sum_pred"] / b["count"],
                "actual_rate": b["sum_outcome"] / b["count"],
            })
        result["buckets"] = bucket_list

    return result


async def get_category_stats(min_count: int = 3) -> list[dict]:
    """Per-category Brier scores — mirrors run_category_analysis.py logic."""
    async with get_db(read_only=True) as db:
        async with db.execute(
            """SELECT c.brier_score, c.actual_outcome,
                      m.tags, m.current_price
               FROM calibration c
               JOIN predictions p ON c.prediction_id = p.id
               JOIN markets m ON p.market_id = m.id
               WHERE c.actual_outcome IS NOT NULL
                 AND c.brier_score IS NOT NULL"""
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return []

    category_data: dict[str, list[dict]] = {}
    for row in rows:
        tags_raw = row["tags"]
        tags: list[str] = json.loads(tags_raw) if tags_raw else ["uncategorized"]

        entry = {
            "brier": row["brier_score"],
            "market_brier": (row["current_price"] - row["actual_outcome"]) ** 2,
        }
        for tag in tags:
            category_data.setdefault(tag, []).append(entry)

    results = []
    for category, entries in sorted(category_data.items()):
        if len(entries) < min_count:
            continue

        mean_brier = sum(e["brier"] for e in entries) / len(entries)
        market_brier = sum(e["market_brier"] for e in entries) / len(entries)

        try:
            skill = brier_skill_score(mean_brier, market_brier)
        except ValueError:
            skill = 0.0

        results.append({
            "category": category,
            "count": len(entries),
            "mean_brier": mean_brier,
            "market_brier": market_brier,
            "skill_score": skill,
        })

    results.sort(key=lambda s: s["skill_score"], reverse=True)
    return results


async def get_trades(page: int = 1, per_page: int = 20) -> tuple[list[dict], dict, int]:
    """Paginated trades with summary stats."""
    offset = (page - 1) * per_page
    async with get_db(read_only=True) as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM trades") as cur:
            total = (await cur.fetchone())["cnt"]

        async with db.execute(
            """SELECT t.*, m.question
               FROM trades t
               JOIN markets m ON t.market_id = m.id
               ORDER BY t.timestamp DESC
               LIMIT ? OFFSET ?""",
            (per_page, offset),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        # Summary
        async with db.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                      SUM(pnl) as total_pnl
               FROM trades"""
        ) as cur:
            summary_row = await cur.fetchone()
            summary = {
                "total": summary_row["total"],
                "wins": summary_row["wins"] or 0,
                "total_pnl": summary_row["total_pnl"] or 0.0,
                "win_rate": (
                    (summary_row["wins"] or 0) / summary_row["total"]
                    if summary_row["total"]
                    else 0.0
                ),
            }

    return rows, summary, total


async def get_cost_log() -> tuple[list[dict], float]:
    """API cost log ordered by date, plus running total."""
    async with get_db(read_only=True) as db:
        async with db.execute(
            "SELECT * FROM api_cost_log ORDER BY date DESC"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT SUM(est_cost_usd) as total FROM api_cost_log"
        ) as cur:
            total = (await cur.fetchone())["total"] or 0.0

    return rows, total
