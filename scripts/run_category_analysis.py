#!/usr/bin/env python3
"""Per-category Brier score analysis — find where Claude has edge.

Usage:
    python scripts/run_category_analysis.py              # all resolved predictions
    python scripts/run_category_analysis.py --min 5      # only categories with ≥5 predictions

Joins calibration scores with market tags to show Brier scores grouped by
category. Highlights categories where Claude beats (or loses to) the market.
"""
import argparse
import asyncio
import json
import logging
from dataclasses import dataclass

from src.db.connection import get_db
from src.tracking.calibration import brier_skill_score
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class CategoryStats:
    category: str
    count: int
    mean_brier: float
    market_brier: float
    skill_score: float


async def analyse(min_count: int = 3) -> list[CategoryStats]:
    """Compute per-category Brier scores from resolved predictions."""
    async with get_db() as db:
        # Get all resolved calibration entries joined with market tags and price
        async with db.execute(
            """SELECT
                   c.predicted_prob,
                   c.actual_outcome,
                   c.brier_score,
                   m.tags,
                   p.market_id,
                   (SELECT current_price FROM markets WHERE id = p.market_id) as market_price
               FROM calibration c
               JOIN predictions p ON c.prediction_id = p.id
               JOIN markets m ON p.market_id = m.id
               WHERE c.actual_outcome IS NOT NULL
                 AND c.brier_score IS NOT NULL"""
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        logger.warning("No resolved predictions found — nothing to analyse")
        return []

    # Accumulate per-category stats
    # Each market can have multiple tags; count it once per tag
    category_data: dict[str, list[dict]] = {}

    for row in rows:
        tags_raw = row["tags"]
        tags: list[str] = json.loads(tags_raw) if tags_raw else ["uncategorized"]

        entry = {
            "brier": row["brier_score"],
            "market_brier": (row["market_price"] - row["actual_outcome"]) ** 2,
        }

        for tag in tags:
            category_data.setdefault(tag, []).append(entry)

    # Compute stats per category
    results: list[CategoryStats] = []
    for category, entries in sorted(category_data.items()):
        if len(entries) < min_count:
            continue

        mean_brier = sum(e["brier"] for e in entries) / len(entries)
        market_brier = sum(e["market_brier"] for e in entries) / len(entries)

        try:
            skill = brier_skill_score(mean_brier, market_brier)
        except ValueError:
            skill = 0.0

        results.append(CategoryStats(
            category=category,
            count=len(entries),
            mean_brier=mean_brier,
            market_brier=market_brier,
            skill_score=skill,
        ))

    # Sort by skill score descending — best categories first
    results.sort(key=lambda s: s.skill_score, reverse=True)
    return results


def print_report(stats: list[CategoryStats]) -> None:
    """Print a formatted category analysis report."""
    if not stats:
        print("No categories with enough resolved predictions to analyse.")
        return

    print("\n" + "=" * 75)
    print("PER-CATEGORY BRIER SCORE ANALYSIS")
    print("=" * 75)
    print(f"{'Category':<30} {'N':>4} {'Claude':>8} {'Market':>8} {'Skill':>8}")
    print("-" * 75)

    for s in stats:
        # Skill score indicator
        if s.skill_score > 0.05:
            indicator = " ✓"
        elif s.skill_score < -0.05:
            indicator = " ✗"
        else:
            indicator = "  "

        print(
            f"{s.category[:30]:<30} {s.count:>4} "
            f"{s.mean_brier:>8.4f} {s.market_brier:>8.4f} "
            f"{s.skill_score:>+7.2f}{indicator}"
        )

    print("-" * 75)

    # Overall summary
    total_n = sum(s.count for s in stats)
    beating = [s for s in stats if s.skill_score > 0.05]
    losing = [s for s in stats if s.skill_score < -0.05]

    print(f"\nTotal resolved predictions: {total_n}")
    print(f"Categories where Claude beats market: {len(beating)}")
    print(f"Categories where market beats Claude: {len(losing)}")

    if beating:
        print(f"\nBest category: {beating[0].category} (skill: {beating[0].skill_score:+.2f}, n={beating[0].count})")
    if losing:
        print(f"Worst category: {losing[-1].category} (skill: {losing[-1].skill_score:+.2f}, n={losing[-1].count})")

    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Per-category Brier score analysis")
    parser.add_argument(
        "--min",
        type=int,
        default=3,
        help="Minimum predictions per category to include (default: 3)",
    )
    return parser.parse_args()


async def main() -> None:
    setup_logging()
    args = parse_args()
    stats = await analyse(min_count=args.min)
    print_report(stats)


if __name__ == "__main__":
    asyncio.run(main())
