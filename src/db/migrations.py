"""Simple schema versioning — applies SCHEMA if tables don't exist yet."""
import aiosqlite

from src.db.models import SCHEMA


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply schema to an open DB connection. Safe to call on every startup."""
    await db.executescript(SCHEMA)
    await db.commit()
