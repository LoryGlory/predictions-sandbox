#!/usr/bin/env python3
"""Phase 0: does a favorite-longshot bias exist on Manifold?

Pre-registered in experiment-longshot-bias.md. No Claude API calls — this is
pure market-price analysis and costs nothing but time and Manifold requests.

METHOD (frozen before any data was pulled):
  · Sample:   resolved binary markets pulled fresh from /v0/markets, NOT from
              predictions.db (that corpus is whitelist-contaminated)
  · Price:    reconstructed from trade history at 24h before close — never the
              `probability` field, which for a resolved market IS the outcome
  · Buckets:  see src/tracking/price_buckets.py (frozen)
  · Power:    300 resolved markets per bucket to count as evidence
  · Junk:     unfiltered AND is_low_signal-filtered tables reported as
              CO-PRIMARY; disagreement between them is a finding, not something
              to resolve by preferring the nicer one

Phase −1 (scripts/longshot_pilot.py) is the cost gate and comes first.

SCALE — measured on a 120-market reconnaissance sample, 2026-08-14:
  · 40% of resolved binary markets have NO trade more than 24h before close.
    Manifold is full of short-fuse markets ("will I eat a crayon in the next
    hour") that never existed 24h ahead of their own close. They are excluded
    by construction, so a pull must be ~1.7x the usable target.
  · Price distribution is heavily centre-weighted. In that sample the 50-65%
    band held 35% of usable markets while the 5-10% band held 1.6%.
  · Consequence: the band H1 is actually about — sub-10% — is the RAREST.
    Projected pull to reach N=300 there is 10,000-30,000 markets (the estimate
    is rough; it rests on a single observation in that bucket). That is
    10-30k /v0/bets calls, i.e. a multi-hour unattended run, not minutes.
  · The recon sample was drawn from the most recent markets and may skew
    short-fuse. A historical pull could distribute differently.

So --target defaults high deliberately. Expect the low bands to be the
binding constraint and expect to report them as underpowered on a first run.

Usage:
    python scripts/longshot_phase0.py --fetch --target 20000  # long, resumable
    python scripts/longshot_phase0.py --analyze               # read the tables
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from src.markets.manifold import ManifoldClient
from src.markets.price_history import (
    PriceHistoryCache,
    cutoff_for_close,
    fetch_price_at_cutoff,
)
from src.markets.scanner import is_low_signal
from src.tracking.logger import setup_logging
from src.tracking.price_buckets import analyze_buckets, format_bucket_table

logger = logging.getLogger(__name__)

# ── FROZEN 2026-08-14 by experiment-longshot-bias.md ────────────────────
CUTOFF_HOURS = 24
# ────────────────────────────────────────────────────────────────────────

CACHE_ROOT = Path(".cache/longshot")
MARKETS_FILE = CACHE_ROOT / "resolved_markets.json"


def _is_usable_sample(market: dict, seen: set[str]) -> bool:
    """Resolved binary market with a close time, not already collected.

    Deliberately does NOT filter on content — junk filtering happens at
    analysis time so the filtered and unfiltered tables share one sample.
    """
    return bool(
        market.get("isResolved")
        and market.get("outcomeType") == "BINARY"
        and market.get("resolution") in ("YES", "NO")
        and market.get("closeTime")
        and market.get("id") not in seen
    )


async def fetch_resolved_markets(
    client: ManifoldClient,
    target: int,
    max_pages: int = 400,
) -> list[dict]:
    """Page through /v0/markets collecting resolved binary markets.

    The backtest helper of the same name caps at 10 pages, which tops out
    around a few hundred markets — nowhere near the 300-per-bucket power
    requirement across ten buckets. This one pages properly and checkpoints
    to disk so an interrupted pull resumes instead of restarting.

    No content filtering happens here beyond "resolved binary with a close
    time". Junk filtering is applied at ANALYSIS time so that both the
    filtered and unfiltered tables come from one identical sample.
    """
    MARKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    resolved: list[dict] = []
    seen: set[str] = set()
    before: str | None = None

    if MARKETS_FILE.exists():
        resolved = json.loads(MARKETS_FILE.read_text())
        seen = {m["id"] for m in resolved}
        if resolved:
            before = resolved[-1]["_page_cursor"]
            logger.info("Resuming from cache: %d markets already pulled", len(resolved))

    for page in range(max_pages):
        if len(resolved) >= target:
            break
        batch = await client.get_markets(limit=1000, before=before)
        if not batch:
            logger.info("Reached the end of available markets")
            break

        for market in batch:
            if _is_usable_sample(market, seen):
                seen.add(market["id"])
                resolved.append({
                    "id": market["id"],
                    "question": market.get("question", ""),
                    "resolution": market["resolution"],
                    "closeTime": market["closeTime"],
                    "_page_cursor": batch[-1]["id"],
                })

        before = batch[-1]["id"]
        if page % 10 == 0:
            MARKETS_FILE.write_text(json.dumps(resolved))
            logger.info("Page %d — %d resolved binary markets collected", page, len(resolved))

    MARKETS_FILE.write_text(json.dumps(resolved))
    logger.info("Collected %d resolved binary markets", len(resolved))
    return resolved


async def build_price_cache(markets: list[dict], concurrency: int = 8) -> None:
    """Reconstruct and cache the 24h-before-close price for every market."""
    cache = PriceHistoryCache(CACHE_ROOT, CUTOFF_HOURS)
    todo = [m for m in markets if cache.get(m["id"]) is None]
    logger.info(
        "Price reconstruction: %d markets total, %d already cached, %d to fetch",
        len(markets), len(markets) - len(todo), len(todo),
    )
    if not todo:
        return

    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async with ManifoldClient() as client:
        async def one(market: dict) -> None:
            nonlocal done
            async with semaphore:
                cutoff = cutoff_for_close(market["closeTime"], CUTOFF_HOURS)
                try:
                    await fetch_price_at_cutoff(client, market["id"], cutoff, cache)
                except Exception:
                    logger.debug("Price fetch failed for %s", market["id"], exc_info=True)
                done += 1
                if done % 250 == 0:
                    logger.info("  reconstructed %d/%d", done, len(todo))

        await asyncio.gather(*(one(m) for m in todo))


def analyze() -> int:
    """Read the cache and print the two co-primary tables."""
    if not MARKETS_FILE.exists():
        print("No market sample cached. Run with --fetch first.")
        return 1

    markets = json.loads(MARKETS_FILE.read_text())
    cache = PriceHistoryCache(CACHE_ROOT, CUTOFF_HOURS)

    unfiltered: list[tuple[float, int]] = []
    filtered: list[tuple[float, int]] = []
    missing = 0

    for market in markets:
        snap = cache.get(market["id"])
        if snap is None or not snap.usable:
            missing += 1
            continue
        outcome = 1 if market["resolution"] == "YES" else 0
        unfiltered.append((snap.price, outcome))
        if not is_low_signal(market.get("question", "")):
            filtered.append((snap.price, outcome))

    print("=" * 78)
    print("PHASE 0 — FAVORITE-LONGSHOT BIAS ON MANIFOLD")
    print("=" * 78)
    print(f"Resolved binary markets sampled:      {len(markets)}")
    print(f"Excluded (no trade before cutoff):    {missing}")
    print(f"Usable observations:                  {len(unfiltered)}")
    print(f"Price snapshot:                       {CUTOFF_HOURS}h before close")
    print()
    print("Both tables below are CO-PRIMARY. Where they disagree, that")
    print("disagreement is the finding — do not pick the nicer one.")
    print()
    print(format_bucket_table(
        analyze_buckets(unfiltered),
        f"TABLE A — unfiltered (N={len(unfiltered)})",
    ))
    print()
    print(format_bucket_table(
        analyze_buckets(filtered),
        f"TABLE B — is_low_signal filtered (N={len(filtered)}, "
        f"{len(unfiltered) - len(filtered)} junk markets removed)",
    ))
    print()
    print("H1 predicts NEGATIVE diffs in the low bands. Read Table A and B")
    print("together, and report underpowered buckets as underpowered.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true",
                        help="pull markets and reconstruct prices into the cache")
    parser.add_argument("--analyze", action="store_true",
                        help="print the bucket tables from cached data")
    parser.add_argument("--target", type=int, default=20000,
                        help="resolved markets to collect (default 20000 — see "
                             "the SCALE note in this file's docstring)")
    args = parser.parse_args()

    if not args.fetch and not args.analyze:
        parser.error("choose --fetch and/or --analyze")

    setup_logging()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    if args.fetch:
        async with ManifoldClient() as client:
            markets = await fetch_resolved_markets(client, target=args.target)
        await build_price_cache(markets)

    if args.analyze:
        return analyze()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
