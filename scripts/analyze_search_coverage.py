#!/usr/bin/env python3
"""Retrospective analysis — how many past predictions would trigger web search?

Runs the current needs_realtime_search() rules against every resolved prediction
in the DB and reports:
  1. How many would trigger search (coverage)
  2. How Claude performed on those specifically (Brier + skill)

This is a cheap check with no API calls — we're just asking: "given the rules
we ship today, what fraction of the back data would have gotten search enabled,
and did Claude do notably worse on that subset?"

If Claude's Brier is significantly higher on the search-eligible subset than
the baseline, web search has a clear target. If it's similar, search may not
meaningfully help even when it fires.

Usage:
    python scripts/analyze_search_coverage.py
"""
import asyncio
import json

from src.db.connection import get_db
from src.markets.scanner import needs_realtime_search
from src.tracking.calibration import brier_skill_score


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _partition_by_eligibility(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split resolved predictions into (search-eligible, non-eligible)."""
    eligible: list[dict] = []
    not_eligible: list[dict] = []
    for r in rows:
        tags = _parse_tags(r["tags"])
        if needs_realtime_search(tags, r["question"]):
            eligible.append(r)
        else:
            not_eligible.append(r)
    return eligible, not_eligible


async def main() -> None:
    async with get_db(read_only=True) as db:
        async with db.execute(
            """SELECT
                   m.question, m.tags,
                   p.estimated_prob, p.market_price,
                   c.actual_outcome, c.brier_score
               FROM calibration c
               JOIN predictions p ON c.prediction_id = p.id
               JOIN markets m ON p.market_id = m.id
               WHERE c.brier_score IS NOT NULL
                 AND p.market_price IS NOT NULL"""
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    eligible, not_eligible = _partition_by_eligibility(rows)

    print(f"\n{'=' * 72}")
    print(f"SEARCH COVERAGE — {len(rows)} resolved predictions")
    print(f"{'=' * 72}\n")
    print(f"Would trigger search: {len(eligible)} ({100*len(eligible)/len(rows):.1f}%)")
    print(f"Would NOT trigger:    {len(not_eligible)} ({100*len(not_eligible)/len(rows):.1f}%)\n")

    def _stats(label: str, subset: list[dict]) -> None:
        if not subset:
            print(f"{label}: no samples")
            return
        claude = sum(r["brier_score"] for r in subset) / len(subset)
        market = sum(
            (r["market_price"] - r["actual_outcome"]) ** 2 for r in subset
        ) / len(subset)
        try:
            skill = brier_skill_score(claude, market)
        except ValueError:
            skill = 0.0
        print(
            f"{label:<22} N={len(subset):>4}  Claude {claude:.4f}  "
            f"Market {market:.4f}  Skill {skill:+.3f}"
        )

    _stats("Search-eligible:", eligible)
    _stats("Non-eligible:", not_eligible)
    _stats("Overall:", rows)

    print("\nInterpretation:")
    print("  If search-eligible subset has meaningfully WORSE Claude Brier than")
    print("  non-eligible, web search has a clear target — fixing those predictions")
    print("  should lift the overall skill score. If similar, search may not help much.")

    # Show a few worst-performers from the eligible set (potential fix candidates)
    if eligible:
        worst = sorted(eligible, key=lambda r: r["brier_score"], reverse=True)[:5]
        print("\nTop 5 worst-performing search-eligible predictions:")
        print(f"  {'Brier':<8} {'Est':<6} {'Mkt':<6} {'Actual':<8} Question")
        for r in worst:
            outcome = "YES" if r["actual_outcome"] == 1 else "NO"
            print(
                f"  {r['brier_score']:<8.3f} "
                f"{r['estimated_prob']:<6.2f} {r['market_price']:<6.2f} "
                f"{outcome:<8} {r['question'][:50]}"
            )


if __name__ == "__main__":
    asyncio.run(main())
