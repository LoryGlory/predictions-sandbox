"""DB connection manager.

Usage:
    async with get_db() as db:
        await db.execute(...)
"""
from contextlib import asynccontextmanager

import aiosqlite

from config.settings import settings
from src.db.migrations import run_migrations

# Track which DB paths have been migrated in this process so migrations only run once.
_migrated_paths: set[str] = set()


@asynccontextmanager
async def get_db(read_only: bool = False, path: str | None = None):
    """Async context manager for a DB connection.

    Runs migrations once per DB path per process (on first non-read-only open).
    Pass read_only=True for the dashboard to prevent accidental writes.
    Pass path to override the default DB path (useful in tests).
    """
    db_path = path or settings.db_path
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA foreign_keys = ON")
        if not read_only and db_path not in _migrated_paths:
            await run_migrations(db)
            _migrated_paths.add(db_path)
        yield db
    finally:
        await db.close()
