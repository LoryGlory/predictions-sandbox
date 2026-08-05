"""Simple schema versioning — applies SCHEMA if tables don't exist yet."""
import aiosqlite

from src.db.models import SCHEMA


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply schema to an open DB connection. Safe to call on every startup."""
    await db.executescript(SCHEMA)

    # Add columns to predictions if missing (existing DBs).
    async with db.execute("PRAGMA table_info(predictions)") as cur:
        columns = {row[1] async for row in cur}
    if "market_price" not in columns:
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN market_price REAL"
        )
    if "prompt_version" not in columns:
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN prompt_version TEXT"
        )
    if "used_web_search" not in columns:
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN used_web_search INTEGER NOT NULL DEFAULT 0"
        )
    if "ensemble_samples" not in columns:
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN ensemble_samples TEXT"
        )
    if "scenarios" not in columns:
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN scenarios TEXT"
        )

    # Trades table: live_bet_id for tracking real Manifold bets
    async with db.execute("PRAGMA table_info(trades)") as cur:
        trade_cols = {row[1] async for row in cur}
    if "live_bet_id" not in trade_cols:
        await db.execute("ALTER TABLE trades ADD COLUMN live_bet_id TEXT")
    # Actual fill data from the Manifold bet response — used for exact
    # live-trade P&L instead of pre-bet price + requested size.
    if "filled_amount" not in trade_cols:
        await db.execute("ALTER TABLE trades ADD COLUMN filled_amount REAL")
    if "prob_after" not in trade_cols:
        await db.execute("ALTER TABLE trades ADD COLUMN prob_after REAL")
    if "shares" not in trade_cols:
        await db.execute("ALTER TABLE trades ADD COLUMN shares REAL")

    await db.commit()
