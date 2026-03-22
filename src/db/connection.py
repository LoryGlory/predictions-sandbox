"""DB connection manager.

Usage:
    async with get_db() as db:
        await db.execute(...)
"""
from contextlib import asynccontextmanager

import aiosqlite

from config.settings import settings
from src.db.migrations import run_migrations


@asynccontextmanager
async def get_db(read_only: bool = False):
    """Async context manager for a DB connection.

    Runs migrations on first open so the schema is always up to date.
    Pass read_only=True for the dashboard to prevent accidental writes.
    """
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    try:
        if not read_only:
            await run_migrations(db)
        yield db
    finally:
        await db.close()
