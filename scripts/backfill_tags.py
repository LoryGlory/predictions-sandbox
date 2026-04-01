#!/usr/bin/env python3
"""Backfill tags for markets missing them by fetching full details from Manifold.

Usage:
    python scripts/backfill_tags.py
"""
import asyncio
import json
import logging

from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)


async def backfill() -> None:
    setup_logging()

    async with get_db() as db:
        async with db.execute(
            "SELECT id, external_id FROM markets WHERE tags IS NULL AND external_id IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        print("All markets already have tags.")
        return

    print(f"Backfilling tags for {len(rows)} markets...")
    updated = 0

    async with ManifoldClient() as client:
        for row in rows:
            try:
                full = await client.get_market(row["external_id"])
                raw_tags = full.get("groupSlugs") or full.get("tags") or []
                tags_json = json.dumps(raw_tags) if raw_tags else None

                async with get_db() as db:
                    await db.execute(
                        "UPDATE markets SET tags = ? WHERE id = ?",
                        (tags_json, row["id"]),
                    )
                    await db.commit()

                updated += 1
                if updated % 20 == 0:
                    print(f"  {updated}/{len(rows)} done...")
            except Exception as e:
                logger.warning("Failed to fetch tags for %s: %s", row["external_id"], e)

    print(f"Done. Updated {updated}/{len(rows)} markets.")


if __name__ == "__main__":
    asyncio.run(backfill())
