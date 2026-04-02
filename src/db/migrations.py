"""Simple schema versioning — applies SCHEMA if tables don't exist yet."""
import aiosqlite

from src.db.models import SCHEMA


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply schema to an open DB connection. Safe to call on every startup."""
    await db.executescript(SCHEMA)

    # Add market_price column to predictions if missing (existing DBs).
    async with db.execute("PRAGMA table_info(predictions)") as cur:
        columns = {row[1] async for row in cur}
    if "market_price" not in columns:
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN market_price REAL"
        )

    await db.commit()
