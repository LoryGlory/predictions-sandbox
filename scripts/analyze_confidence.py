#!/usr/bin/env python3
"""Retrospective analysis — does Claude's self-reported confidence track accuracy?

Groups resolved predictions by Claude's `confidence` field and shows skill score
per bucket. If high-confidence predictions consistently beat the market and
low-confidence ones don't, confidence-weighted Kelly makes sense.

Usage:
    python scripts/analyze_confidence.py
"""
import asyncio

from src.db.connection import get_db
from src.tracking.calibration import brier_skill_score


async def main() -> None:
    async with get_db(read_only=True) as db:
        async with db.execute(
            """SELECT
                   COALESCE(LOWER(TRIM(p.confidence)), 'missing') as conf,
                   p.estimated_prob, p.market_price, c.actual_outcome, c.brier_score
               FROM calibration c
               JOIN predictions p ON c.prediction_id = p.id
               JOIN markets m ON p.market_id = m.id
               WHERE c.brier_score IS NOT NULL
                 AND p.market_price IS NOT NULL"""
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    buckets: dict[str, dict] = {}
    for r in rows:
        b = buckets.setdefault(r["conf"], {"briers": [], "market_briers": []})
        b["briers"].append(r["brier_score"])
        b["market_briers"].append((r["market_price"] - r["actual_outcome"]) ** 2)

    print(f"\n{'=' * 72}")
    print(f"CONFIDENCE ANALYSIS — {len(rows)} resolved predictions")
    print(f"{'=' * 72}\n")
    print(f"{'Confidence':<12} {'N':>5} {'Claude B.':>10} {'Market B.':>10} {'Skill':>8}")
    print("-" * 52)

    order = ["high", "medium", "low", "missing"]
    for conf in order + [k for k in buckets if k not in order]:
        if conf not in buckets:
            continue
        b = buckets[conf]
        n = len(b["briers"])
        mean_claude = sum(b["briers"]) / n
        mean_market = sum(b["market_briers"]) / n
        try:
            skill = brier_skill_score(mean_claude, mean_market)
        except ValueError:
            skill = 0.0
        skill_marker = " ← beats market" if skill > 0 else ""
        print(
            f"{conf:<12} {n:>5} {mean_claude:>10.4f} {mean_market:>10.4f} "
            f"{skill:>+8.3f}{skill_marker}"
        )

    print()
    if "high" in buckets and "low" in buckets:
        high_n = len(buckets["high"]["briers"])
        low_n = len(buckets["low"]["briers"])
        print(f"Interpretation: if high ({high_n} samples) has meaningfully better")
        print(f"skill than low ({low_n} samples), confidence-weighted Kelly helps.")
        print("If they're similar, Claude's confidence field is not a useful signal.")


if __name__ == "__main__":
    asyncio.run(main())
