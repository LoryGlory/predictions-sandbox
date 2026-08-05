#!/usr/bin/env python3
"""Reconcile Manifold's bet history against our trades table.

The live executor can end up out of sync with Manifold in two ways:
  1. UNTRACKED: a bet exists on Manifold but not in our DB — e.g. the process
     died between the API call and the INSERT, or a lost response caused a
     placed bet to go unrecorded.
  2. UNCONFIRMED: a DB row has is_paper=0 but live_bet_id IS NULL — the
     executor recorded "state unknown" after an API exception, and we don't
     know whether the bet actually exists on Manifold.

This script is READ-ONLY: it reports discrepancies and suggests likely
matches, but never modifies the DB or places/cancels bets. Fixing is a
deliberate human decision.

Usage:
    python scripts/reconcile_manifold.py

Exit code 0 = fully reconciled, 1 = discrepancies found (cron-friendly).
"""
import asyncio
import logging
import sys

from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)


async def _load_db_live_trades() -> list[dict]:
    async with get_db(read_only=True) as db:
        async with db.execute(
            """SELECT t.id, t.live_bet_id, t.direction, t.size, t.timestamp,
                      m.external_id AS market_external_id, m.question
               FROM trades t
               JOIN markets m ON t.market_id = m.id
               WHERE t.is_paper = 0
               ORDER BY t.timestamp"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _describe_bet(bet: dict) -> str:
    return (
        f"{bet.get('outcome', '?')} M${bet.get('amount', 0):.0f} "
        f"on {bet.get('contractId', '?')} (bet id {bet.get('id', '?')})"
    )


async def reconcile() -> int:
    """Run the reconciliation. Returns the number of discrepancies found."""
    setup_logging()

    async with ManifoldClient() as client:
        me = await client.get_me()
        user_id = me.get("id")
        if not user_id:
            logger.error("Could not determine Manifold user id from /me")
            return 1
        manifold_bets = await client.get_bets(user_id)

    db_trades = await _load_db_live_trades()

    db_bet_ids = {t["live_bet_id"] for t in db_trades if t["live_bet_id"]}
    manifold_by_id = {b["id"]: b for b in manifold_bets if b.get("id")}

    # 1. Bets on Manifold that our DB doesn't know about
    untracked = [b for bid, b in manifold_by_id.items() if bid not in db_bet_ids]

    # 2. DB live rows with no confirmed bet id
    unconfirmed = [t for t in db_trades if not t["live_bet_id"]]

    print("=" * 70)
    print("MANIFOLD RECONCILIATION REPORT")
    print("=" * 70)
    print(f"Manifold bets fetched:   {len(manifold_bets)}")
    print(f"DB live trades:          {len(db_trades)}")
    print(f"Matched by bet id:       {len(db_bet_ids & set(manifold_by_id))}")
    print()

    if untracked:
        print(f"⚠ UNTRACKED — on Manifold but not in DB ({len(untracked)}):")
        for b in untracked:
            print(f"    {_describe_bet(b)}")
        print(
            "    → Real mana was spent that our P&L doesn't see. Either insert\n"
            "      matching trade rows manually or accept the ledger gap."
        )
        print()

    if unconfirmed:
        print(f"⚠ UNCONFIRMED — in DB with no bet id ({len(unconfirmed)}):")
        for t in unconfirmed:
            # Suggest possible matches among untracked Manifold bets on the
            # same market with a similar amount.
            candidates = [
                b for b in untracked
                if b.get("contractId") == t["market_external_id"]
                and abs(float(b.get("amount", 0)) - t["size"]) <= 1.0
            ]
            hint = (
                f" — possible match: {_describe_bet(candidates[0])}"
                if candidates else " — no matching Manifold bet found "
                "(the API call likely genuinely failed; consider marking paper)"
            )
            print(
                f"    trade #{t['id']}: {t['direction']} M${t['size']:.2f} "
                f"on '{t['question'][:45]}' at {t['timestamp']}{hint}"
            )
        print()

    discrepancies = len(untracked) + len(unconfirmed)
    if discrepancies == 0:
        print("✓ Fully reconciled — every Manifold bet has a DB row and vice versa.")
    else:
        print(f"{discrepancies} discrepancie(s) need human review.")
    return discrepancies


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(reconcile()) > 0 else 0)
