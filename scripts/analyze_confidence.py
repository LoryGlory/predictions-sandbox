#!/usr/bin/env python3
"""Retrospective analysis — does Claude's self-reported confidence track accuracy?

Groups resolved predictions by Claude's `confidence` field and shows skill score
per bucket. Also runs a retrospective P&L simulation comparing weighting
strategies against the same resolved predictions.

Usage:
    python scripts/analyze_confidence.py
"""
import asyncio
from collections.abc import Callable

from src.db.connection import get_db
from src.tracking.calibration import brier_skill_score
from src.trading.kelly import confidence_scale, kelly_bet_size

BANKROLL = 50.0
MIN_EDGE = 0.05


async def _load_rows() -> list[dict]:
    async with get_db(read_only=True) as db:
        async with db.execute(
            """SELECT
                   COALESCE(LOWER(TRIM(p.confidence)), 'missing') as conf,
                   p.estimated_prob, p.market_price, c.actual_outcome, c.brier_score
               FROM calibration c
               JOIN predictions p ON c.prediction_id = p.id
               JOIN markets m ON p.market_id = m.id
               WHERE c.brier_score IS NOT NULL
                 AND p.market_price IS NOT NULL
                 AND p.market_price > 0 AND p.market_price < 1"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _print_confidence_table(rows: list[dict]) -> None:
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
        marker = " ← beats market" if skill > 0 else ""
        print(
            f"{conf:<12} {n:>5} {mean_claude:>10.4f} {mean_market:>10.4f} "
            f"{skill:>+8.3f}{marker}"
        )


def _simulate_strategy(rows: list[dict], weight_fn: Callable[[str], float]) -> tuple[int, int, float]:
    """Return (trades, wins, total_pnl) for a given confidence weighting strategy."""
    trades = 0
    wins = 0
    total_pnl = 0.0
    for r in rows:
        edge = r["estimated_prob"] - r["market_price"]
        if abs(edge) < MIN_EDGE:
            continue
        direction_yes = edge > 0
        our_prob = r["estimated_prob"] if direction_yes else 1 - r["estimated_prob"]
        mkt_price = r["market_price"] if direction_yes else 1 - r["market_price"]

        bet = kelly_bet_size(
            our_prob=our_prob,
            market_price=mkt_price,
            bankroll=BANKROLL,
            kelly_fraction_multiplier=0.25,
            max_position_pct=0.05,
        ) * weight_fn(r["conf"])

        if bet <= 0:
            continue
        trades += 1
        won = (direction_yes and r["actual_outcome"] == 1) or \
              (not direction_yes and r["actual_outcome"] == 0)
        if won:
            wins += 1
            total_pnl += bet * (1 / mkt_price - 1)
        else:
            total_pnl -= bet
    return trades, wins, total_pnl


def _print_pnl_simulation(rows: list[dict]) -> None:
    print(f"\n{'=' * 72}")
    print(f"P&L SIMULATION — quarter Kelly, ${BANKROLL:.0f} bankroll, {MIN_EDGE} edge threshold")
    print(f"{'=' * 72}")
    print("Bet at market_price on predictions with |edge| >= threshold.")
    print("Payout = 1.0 if direction matches outcome, else 0.\n")

    strategies: dict[str, Callable[[str], float]] = {
        "uniform (1.0 all)":      lambda _: 1.0,
        "old (high=1, low=0.25)": lambda c: {"high": 1.0, "medium": 0.6, "low": 0.25}.get(c, 0.6),
        "new (inverted)":         confidence_scale,
    }

    print(f"{'Strategy':<28} {'Trades':>8} {'Wins':>6} {'Win%':>7} {'Total P&L':>12}")
    print("-" * 68)

    for name, weight_fn in strategies.items():
        trades, wins, pnl = _simulate_strategy(rows, weight_fn)
        win_pct = 100 * wins / trades if trades else 0
        print(f"{name:<28} {trades:>8} {wins:>6} {win_pct:>6.1f}% {pnl:>+12.2f}")

    print("\nNote: real execution has spreads, slippage, and API costs.")
    print("These are idealized numbers — directional, not precise.")


async def main() -> None:
    rows = await _load_rows()
    _print_confidence_table(rows)
    _print_pnl_simulation(rows)


if __name__ == "__main__":
    asyncio.run(main())
