"""Story collector — captures noteworthy pipeline events for blog posts.

Runs passively alongside the main pipeline. Zero performance impact — just
INSERT statements after predictions and resolutions.
"""
import json
import logging

logger = logging.getLogger(__name__)

# Thresholds for capturing stories
BIG_EDGE_THRESHOLD = 0.20
SPECTACULAR_WIN_BRIER = 0.10
SPECTACULAR_WIN_MARKET_BRIER = 0.20
SPECTACULAR_FAIL_BRIER = 0.30
SPECTACULAR_FAIL_MARKET_BRIER = 0.05
CATEGORY_SKILL_MILESTONE = 0.20
COST_MILESTONES = [1.0, 5.0, 10.0, 25.0, 50.0]


async def check_big_edge(db, prediction: dict, market: dict) -> None:
    """Capture predictions where |edge| > 20%."""
    edge = abs(prediction["estimated_prob"] - prediction["market_price"])
    if edge <= BIG_EDGE_THRESHOLD:
        return

    direction = "above" if prediction["estimated_prob"] > prediction["market_price"] else "below"
    title = (
        f"Big edge: Claude est {prediction['estimated_prob']:.0%} "
        f"({direction} market {prediction['market_price']:.0%}) on '{market['question'][:60]}'"
    )
    details = json.dumps({
        "question": market["question"],
        "estimated_prob": prediction["estimated_prob"],
        "market_price": prediction["market_price"],
        "edge": edge,
        "reasoning": prediction.get("reasoning", ""),
        "prompt_version": prediction.get("prompt_version", ""),
    })

    await _insert_story(db, "big_edge", title, details, blog_post="part2")


async def check_spectacular_result(db, calibration_row: dict) -> None:
    """Capture spectacular wins and failures after resolution."""
    claude_brier = calibration_row.get("brier_score")
    market_price = calibration_row.get("market_price")
    actual = calibration_row.get("actual_outcome")
    question = calibration_row.get("question", "")

    if claude_brier is None or market_price is None or actual is None:
        return

    market_brier = (market_price - actual) ** 2

    details = json.dumps({
        "question": question,
        "estimated_prob": calibration_row.get("estimated_prob"),
        "market_price": market_price,
        "actual_outcome": actual,
        "claude_brier": claude_brier,
        "market_brier": market_brier,
    })

    # Spectacular win: Claude right, market wrong
    if claude_brier < SPECTACULAR_WIN_BRIER and market_brier > SPECTACULAR_WIN_MARKET_BRIER:
        outcome_str = "YES" if actual == 1 else "NO"
        title = (
            f"Spectacular win: '{question[:50]}' — "
            f"Claude Brier {claude_brier:.2f} vs Market {market_brier:.2f} (actual {outcome_str})"
        )
        await _insert_story(db, "spectacular_win", title, details, blog_post="part3")

    # Spectacular failure: Claude very wrong, market was right
    elif claude_brier > SPECTACULAR_FAIL_BRIER and market_brier < SPECTACULAR_FAIL_MARKET_BRIER:
        outcome_str = "YES" if actual == 1 else "NO"
        title = (
            f"Spectacular failure: '{question[:50]}' — "
            f"Claude Brier {claude_brier:.2f} vs Market {market_brier:.2f} (actual {outcome_str})"
        )
        await _insert_story(db, "spectacular_failure", title, details, blog_post="part3")


async def check_cost_milestone(db) -> None:
    """Capture when total API spend crosses a milestone."""
    async with db.execute(
        "SELECT SUM(est_cost_usd) as total FROM api_cost_log"
    ) as cur:
        row = await cur.fetchone()
    total_cost = row["total"] if row and row["total"] else 0.0

    for milestone in COST_MILESTONES:
        if total_cost < milestone:
            break

        # Check if we already captured this milestone
        async with db.execute(
            "SELECT id FROM stories WHERE story_type = 'cost_milestone' AND title LIKE ?",
            (f"%${milestone:.0f}%",),
        ) as cur:
            if await cur.fetchone():
                continue

        # Get current stats
        async with db.execute("SELECT COUNT(*) as cnt FROM predictions") as cur:
            pred_count = (await cur.fetchone())["cnt"]
        async with db.execute(
            "SELECT AVG(brier_score) as avg FROM calibration WHERE actual_outcome IS NOT NULL"
        ) as cur:
            brier_row = await cur.fetchone()
        mean_brier = brier_row["avg"] if brier_row and brier_row["avg"] else None

        title = f"Cost milestone: ${milestone:.0f} total API spend"
        details = json.dumps({
            "milestone": milestone,
            "total_cost": total_cost,
            "total_predictions": pred_count,
            "mean_brier": mean_brier,
        })
        await _insert_story(db, "cost_milestone", title, details, blog_post="part4")


async def check_system_event(db, event_type: str, event_details: str) -> None:
    """Capture notable system events (errors, firsts, etc.)."""
    title = f"System: {event_type}"
    details = json.dumps({"event_type": event_type, "details": event_details})
    await _insert_story(db, "system", title, details, blog_post="part1")


async def get_weekly_digest(db) -> dict | None:
    """Get story counts for the weekly digest (Sundays only)."""
    async with db.execute(
        """SELECT story_type, COUNT(*) as cnt FROM stories
           WHERE created_at >= datetime('now', '-7 days')
           GROUP BY story_type"""
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        return None

    counts = {row["story_type"]: row["cnt"] for row in rows}
    total = sum(counts.values())
    return {"total": total, "counts": counts}


async def _insert_story(
    db, story_type: str, title: str, details: str, blog_post: str | None = None,
) -> None:
    """Insert a story into the stories table."""
    await db.execute(
        """INSERT INTO stories (story_type, title, details, blog_post)
           VALUES (?, ?, ?, ?)""",
        (story_type, title, details, blog_post),
    )
    await db.commit()
    logger.info("Story captured: %s — %s", story_type, title[:80])
