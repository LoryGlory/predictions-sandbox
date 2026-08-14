#!/usr/bin/env python3
"""Phase −1: thirty-minute directional pilot for the favorite-longshot experiment.

THIS IS A PILOT, NOT EVIDENCE. It answers one question — "does the effect even
point in the hypothesised direction?" — so we can decide whether Phase 0 is
worth 6-8 hours. Two independent reasons it cannot settle anything:

  1. SELECTION. predictions.db only contains markets the bot chose to look at
     under a category whitelist. That is the same contamination that killed the
     earlier category-edge claims.
  2. SNAPSHOT MISMATCH. `predictions.market_price` is recorded whenever the
     cron first saw the market — days or weeks before close, not the fixed
     24h-before-close cutoff Phase 0 uses. These buckets are therefore NOT
     comparable to the Phase 0 table and must never be shown beside it as
     though they measured the same thing.

Read the direction and the rough magnitude. Nothing else.

Usage:
    python scripts/longshot_pilot.py
    python scripts/longshot_pilot.py --db /path/to/copy-of-predictions.db
"""
import argparse
import sqlite3
import sys
from pathlib import Path

from src.tracking.price_buckets import analyze_buckets, format_bucket_table

# One row per resolved prediction that carries a pre-resolution price snapshot.
# DISTINCT on market keeps a single observation per market: the pipeline
# re-estimated the same market up to 3x/day, and counting each as an
# independent trial would inflate N and shrink the intervals by a factor of
# roughly sqrt(re-estimates) — the same mistake in a new place.
_QUERY = """
    SELECT p.market_price AS price, c.actual_outcome AS outcome
    FROM calibration c
    JOIN predictions p ON c.prediction_id = p.id
    WHERE c.actual_outcome IS NOT NULL
      AND p.market_price IS NOT NULL
      AND p.market_price > 0 AND p.market_price < 1
      AND p.id = (
          SELECT MIN(p2.id) FROM predictions p2
          WHERE p2.market_id = p.market_id AND p2.market_price IS NOT NULL
      )
"""


def load_observations(db_path: Path) -> list[tuple[float, int]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(_QUERY).fetchall()
    finally:
        conn.close()
    return [(float(price), int(outcome)) for price, outcome in rows]


def _diagnose(db_path: Path) -> list[tuple[str, int]]:
    """Counts that explain WHY a database yielded no usable observations."""
    probes = [
        ("predictions", "SELECT COUNT(*) FROM predictions"),
        ("  with market_price",
         "SELECT COUNT(*) FROM predictions WHERE market_price IS NOT NULL"),
        ("resolved calibrations",
         "SELECT COUNT(*) FROM calibration WHERE actual_outcome IS NOT NULL"),
        ("  joined to a priced prediction",
         "SELECT COUNT(*) FROM calibration c JOIN predictions p "
         "ON c.prediction_id = p.id WHERE c.actual_outcome IS NOT NULL "
         "AND p.market_price IS NOT NULL"),
    ]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out: list[tuple[str, int]] = []
    try:
        for label, sql in probes:
            try:
                out.append((label, conn.execute(sql).fetchone()[0]))
            except sqlite3.Error:
                out.append((label, -1))  # table/column absent in old schemas
    finally:
        conn.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="predictions.db",
        help="path to a predictions.db (default: ./predictions.db)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists() or db_path.stat().st_size == 0:
        print(f"No usable database at {db_path}.", file=sys.stderr)
        print(
            "The live corpus lives on the Pi. Copy it over first, e.g.:\n"
            "  scp laura@100.78.210.94:~/predictions-sandbox/predictions.db /tmp/\n"
            "  python scripts/longshot_pilot.py --db /tmp/predictions.db",
            file=sys.stderr,
        )
        return 1

    observations = load_observations(db_path)
    if not observations:
        print(f"No usable observations in {db_path}.", file=sys.stderr)
        for label, count in _diagnose(db_path):
            print(f"  {label}: {count}", file=sys.stderr)
        print(
            "\nThe pilot needs resolved predictions that also carry a "
            "market_price snapshot.\nOlder databases predate that column being "
            "populated and cannot be used.",
            file=sys.stderr,
        )
        return 1

    print("=" * 78)
    print("PHASE −1 PILOT — DIRECTIONAL ONLY, NOT EVIDENCE")
    print("=" * 78)
    print(f"Source: {db_path}")
    print(f"Observations: {len(observations)} (one per market, first estimate)")
    print()
    print("Contamination, both fatal to using this as a result:")
    print("  · whitelist selection — only markets the bot chose to look at")
    print("  · snapshot is first-seen, NOT the 24h-before-close cutoff Phase 0 uses")
    print()

    results = analyze_buckets(observations)
    print(format_bucket_table(results, "Realized YES rate vs traded price, by band"))

    print()
    print("How to read this:")
    print("  A favorite-longshot bias would show NEGATIVE diffs in the low bands")
    print("  (realized YES rate below the price) and diffs at or above zero high up.")
    print()
    print("Decision gate: if the direction is absent here AND the Kalshi mechanism")
    print("does not transfer to a fee-less CPMM (see the doc), stopping and writing")
    print("the short 'why I did not run this' note is a legitimate outcome.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
