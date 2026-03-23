"""Tests for DB schema creation and basic read/write."""
import aiosqlite
import pytest

from src.db.migrations import run_migrations


@pytest.fixture
async def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as db:
        await run_migrations(db)
        yield db


async def test_migrations_create_tables(tmp_db):
    async with tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ) as cursor:
        tables = {row[0] for row in await cursor.fetchall()}
    assert tables == {"api_cost_log", "calibration", "markets", "predictions", "trades"}


async def test_insert_and_read_market(tmp_db):
    await tmp_db.execute(
        """INSERT INTO markets (platform, question, category, current_price)
           VALUES (?, ?, ?, ?)""",
        ("manifold", "Will X happen?", "politics", 0.42),
    )
    await tmp_db.commit()

    async with tmp_db.execute("SELECT question, current_price FROM markets") as cur:
        row = await cur.fetchone()

    assert row == ("Will X happen?", 0.42)


async def test_get_db_runs_migrations(tmp_path):
    """Test that get_db creates all required tables via migrations."""
    from src.db.connection import get_db

    db_path = str(tmp_path / "test.db")
    async with get_db(path=db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ) as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
    assert tables == {"api_cost_log", "calibration", "markets", "predictions", "trades"}


async def test_get_db_read_only_skips_migrations(tmp_path):
    """Test that read-only mode skips migrations on an empty DB."""
    from src.db.connection import get_db

    db_path = str(tmp_path / "empty.db")
    async with get_db(path=db_path, read_only=True) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ) as cursor:
            tables = await cursor.fetchall()
    assert len(tables) == 0
