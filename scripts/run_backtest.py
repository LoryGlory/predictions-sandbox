#!/usr/bin/env python3
"""Run a calibration backtest against resolved Manifold markets.

Usage:
    python scripts/run_backtest.py              # default: 20 markets
    python scripts/run_backtest.py --count 50  # test against 50 markets

The script fetches already-resolved binary markets, asks Claude to estimate
each probability WITHOUT revealing the outcome, then prints a calibration
report comparing Claude's Brier score against the market-price baseline.
"""
import argparse
import asyncio
import logging

from src.backtesting.backtest import run_backtest
from src.tracking.logger import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest Claude's calibration on resolved markets")
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="Number of resolved markets to test against (default: 20)",
    )
    parser.add_argument(
        "--prompt-version",
        type=str,
        default=None,
        help="Prompt version to use (v1_baseline / v2_market_aware). Default: use active version.",
    )
    parser.add_argument(
        "--with-market-price",
        action="store_true",
        help="Pass market price to estimator (tests v2 Bayesian prior).",
    )
    return parser.parse_args()


async def main() -> None:
    setup_logging()
    args = parse_args()
    logger = logging.getLogger(__name__)

    logger.info(
        "Starting backtest with %d markets (prompt=%s, market_price=%s)",
        args.count,
        args.prompt_version or "active",
        args.with_market_price,
    )
    report = await run_backtest(
        count=args.count,
        prompt_version=args.prompt_version,
        with_market_price=args.with_market_price,
    )
    report.print_report()

    if report.count == 0:
        logger.error("No markets were successfully evaluated")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
