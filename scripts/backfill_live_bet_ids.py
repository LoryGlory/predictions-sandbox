#!/usr/bin/env python3
"""One-time repair: match historical live trades to Manifold's bet ledger.

Reconciliation (2026-08-05) found all 42 live trades had live_bet_id NULL
(the POST /bet response carries the id as "betId", we read "id") and every
recorded stake of M$2.50 was actually M$2 on Manifold (int(round(2.5)) == 2
under banker's rounding). Both bugs are fixed in the executor going forward;
this script repairs the historical rows:

  - fills live_bet_id from the matched Manifold bet
  - corrects size (and filled_amount) to the actual wagered amount
  - stores prob_after and shares from the Manifold ledger
  - recomputes pnl for already-settled trades using exact shares math

Matching is deterministic: trades and bets are grouped by
(market external id, direction) and zipped in chronological order. Groups
whose counts don't line up are reported and left untouched — no guessing.

Dry-run by default. Pass --apply to write changes.

Usage:
    python scripts/backfill_live_bet_ids.py           # show the plan
    python scripts/backfill_live_bet_ids.py --apply   # write it
"""
import argparse
import asyncio
import logging
from collections import defaultdict

from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.tracking.logger import setup_logging
from src.trading.pnl import compute_pnl_from_shares

logger = logging.getLogger(__name__)


async def _load_db_live_trades() -> list[dict]:
    async with get_db(read_only=True) as db:
        async with db.execute(
            """SELECT t.id, t.live_bet_id, t.direction, t.size, t.pnl, t.outcome,
                      t.timestamp, m.external_id AS contract_id
               FROM trades t
               JOIN markets m ON t.market_id = m.id
               WHERE t.is_paper = 0
               ORDER BY t.timestamp"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _match(db_trades: list[dict], manifold_bets: list[dict]) -> tuple[list[tuple[dict, dict]], list[str]]:
    """Pair DB trades with Manifold bets by (contract, direction) in time order.

    Returns (pairs, problems). Groups with mismatched counts produce a problem
    entry and no pairs.
    """
    db_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in db_trades:
        db_groups[(t["contract_id"], t["direction"].lower())].append(t)

    mf_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for b in sorted(manifold_bets, key=lambda b: b.get("createdTime", 0)):
        mf_groups[(b.get("contractId", ""), str(b.get("outcome", "")).lower())].append(b)

    pairs: list[tuple[dict, dict]] = []
    problems: list[str] = []

    for key, trades in sorted(db_groups.items()):
        bets = mf_groups.get(key, [])
        if len(trades) != len(bets):
            problems.append(
                f"{key[0]} ({key[1].upper()}): {len(trades)} DB trades vs "
                f"{len(bets)} Manifold bets — skipped, resolve manually"
            )
            continue
        pairs.extend(zip(trades, bets, strict=True))

    for key in mf_groups:
        if key not in db_groups:
            problems.append(
                f"{key[0]} ({key[1].upper()}): Manifold bets with no DB trades at all"
            )

    return pairs, problems


def _plan_updates(pairs: list[tuple[dict, dict]]) -> list[dict]:
    """Turn matched pairs into concrete UPDATE plans (with recomputed pnl)."""
    updates = []
    for trade, bet in pairs:
        actual_amount = float(bet.get("amount", trade["size"]))
        shares = bet.get("shares")
        new_pnl = trade["pnl"]
        if trade["pnl"] is not None and trade["outcome"] in ("YES", "NO") and shares is not None:
            outcome_int = 1 if trade["outcome"] == "YES" else 0
            new_pnl = compute_pnl_from_shares(
                trade["direction"], float(shares), actual_amount, outcome_int,
            )
        updates.append({
            "trade_id": trade["id"],
            "bet_id": bet.get("id"),
            "old_size": trade["size"],
            "new_size": actual_amount,
            "prob_after": bet.get("probAfter"),
            "shares": shares,
            "old_pnl": trade["pnl"],
            "new_pnl": new_pnl,
        })
    return updates


async def backfill(apply: bool) -> int:
    setup_logging()

    async with ManifoldClient() as client:
        me = await client.get_me()
        manifold_bets = await client.get_bets(me["id"])

    db_trades = await _load_db_live_trades()
    unmatched_db = [t for t in db_trades if not t["live_bet_id"]]

    pairs, problems = _match(unmatched_db, manifold_bets)
    updates = _plan_updates(pairs)

    print(f"DB live trades without bet id: {len(unmatched_db)}")
    print(f"Deterministically matched:     {len(updates)}")
    for p in problems:
        print(f"  ⚠ {p}")

    old_pnl_total = sum(u["old_pnl"] for u in updates if u["old_pnl"] is not None)
    new_pnl_total = sum(u["new_pnl"] for u in updates if u["new_pnl"] is not None)
    print(f"\nSettled P&L before repair: M${old_pnl_total:+.2f}")
    print(f"Settled P&L after repair:  M${new_pnl_total:+.2f}")

    for u in updates:
        pnl_note = (
            f", pnl {u['old_pnl']:+.2f} → {u['new_pnl']:+.2f}"
            if u["old_pnl"] is not None else ""
        )
        print(
            f"  trade #{u['trade_id']}: bet {u['bet_id']}, "
            f"size {u['old_size']:.2f} → {u['new_size']:.2f}{pnl_note}"
        )

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to persist.")
        return 0

    async with get_db() as db:
        for u in updates:
            await db.execute(
                """UPDATE trades
                   SET live_bet_id = ?, size = ?, filled_amount = ?,
                       prob_after = ?, shares = ?, pnl = ?
                   WHERE id = ?""",
                (
                    u["bet_id"], u["new_size"], u["new_size"],
                    u["prob_after"], u["shares"], u["new_pnl"],
                    u["trade_id"],
                ),
            )
        await db.commit()
    print(f"\nApplied {len(updates)} repairs.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(backfill(apply=args.apply)))
