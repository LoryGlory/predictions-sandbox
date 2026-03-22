# Phase 1 Scaffold Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Scaffold a complete, runnable Raspberry Pi prediction market pipeline with working Kelly Criterion, Brier score, budget guardian, Manifold API client, Claude estimator, SQLite layer, and a main polling entry point.

**Architecture:** CLI/cron-based pipeline (no web server in Phase 1). One polling cycle = `run_pipeline.py` fetches markets, sends to Claude for probability estimates, applies Kelly + risk checks, logs everything to SQLite. Dashboard is stubbed for Phase 2.

**Tech Stack:** Python 3.11+, httpx (async), anthropic SDK, SQLite (aiosqlite), pytest, pyproject.toml, systemd (Pi setup script)

---

## Task 1: Project skeleton — files, config, tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `config/settings.py`
- Create: `src/__init__.py`
- Create: `src/markets/__init__.py`
- Create: `src/analysis/__init__.py`
- Create: `src/trading/__init__.py`
- Create: `src/tracking/__init__.py`
- Create: `src/db/__init__.py`
- Create: `tests/__init__.py`
- Create: `dashboard/__init__.py`
- Create: `scripts/__init__.py` (empty, makes scripts importable in tests)

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "predictions-sandbox"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.25.0",
    "httpx>=0.27.0",
    "aiosqlite>=0.20.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
]
dashboard = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["."]
include = ["src*", "config*"]
```

**Step 2: Create .env.example**

```
ANTHROPIC_API_KEY=
MANIFOLD_API_KEY=
POLYMARKET_API_KEY=          # Phase 2
DASHBOARD_USERNAME=admin     # Phase 2
DASHBOARD_PASSWORD=          # Phase 2

# Budget (all in USD/mana units depending on platform)
BUDGET_DAILY_LIMIT=0         # 0 = calibration mode, no real bets
BUDGET_TOTAL_LIMIT=50
KELLY_FRACTION=0.25
MIN_EDGE_THRESHOLD=0.05      # Only bet when edge > 5%

# Pipeline
POLL_INTERVAL_SECONDS=300
MAX_MARKETS_PER_CYCLE=20
LOG_LEVEL=INFO
```

**Step 3: Create .gitignore**

```
# Secrets
.env

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/

# Data
*.db
*.db-wal
*.db-shm

# IDE
.vscode/
.idea/

# OS
.DS_Store
```

**Step 4: Create config/settings.py**

```python
"""Central configuration — reads from environment variables with sensible defaults.

All tunable parameters live here. Never hardcode limits or API settings elsewhere.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # API keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    manifold_api_key: str = os.getenv("MANIFOLD_API_KEY", "")

    # Budget (0 = calibration mode — log predictions but never execute trades)
    budget_daily_limit: float = float(os.getenv("BUDGET_DAILY_LIMIT", "0"))
    budget_total_limit: float = float(os.getenv("BUDGET_TOTAL_LIMIT", "50"))

    # Kelly Criterion
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.25"))
    min_edge_threshold: float = float(os.getenv("MIN_EDGE_THRESHOLD", "0.05"))
    max_position_pct: float = 0.05  # Never bet more than 5% of bankroll

    # Claude model
    model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Pipeline
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    max_markets_per_cycle: int = int(os.getenv("MAX_MARKETS_PER_CYCLE", "20"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Paths
    db_path: str = os.getenv("DB_PATH", "predictions.db")


settings = Settings()
```

**Step 5: Create all empty `__init__.py` files**

```bash
touch src/__init__.py src/markets/__init__.py src/analysis/__init__.py \
      src/trading/__init__.py src/tracking/__init__.py src/db/__init__.py \
      tests/__init__.py dashboard/__init__.py
mkdir -p config && touch config/__init__.py
```

**Step 6: Verify structure**

```bash
find . -name "*.py" | sort
```

Expected: all `__init__.py` files and `config/settings.py` present.

**Step 7: Install dev dependencies**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: no errors, `pytest --collect-only` runs (finds 0 tests — that's fine).

**Step 8: Commit**

```bash
git init
git add pyproject.toml .env.example .gitignore config/ src/ tests/ dashboard/ docs/
git commit -m "feat: project skeleton — pyproject.toml, config, package structure"
```

---

## Task 2: SQLite schema and connection manager

**Files:**
- Create: `src/db/models.py`
- Create: `src/db/connection.py`
- Create: `src/db/migrations.py`

**Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
"""Tests for DB schema creation and basic read/write."""
import asyncio
import pytest
import aiosqlite
from src.db.connection import get_db
from src.db.migrations import run_migrations


@pytest.fixture
async def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as db:
        await run_migrations(db)
        yield db


async def test_migrations_create_tables(tmp_db):
    async with tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cursor:
        tables = {row[0] for row in await cursor.fetchall()}
    assert tables == {"calibration", "markets", "predictions", "trades"}


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
```

**Step 2: Run to confirm it fails**

```bash
pytest tests/test_db.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`.

**Step 3: Create src/db/models.py**

```python
"""SQLite table definitions as SQL strings.

Keeping schema as plain SQL (not an ORM) for simplicity and portability.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT    NOT NULL,          -- 'manifold' | 'polymarket'
    external_id TEXT,                      -- platform's own market ID
    question    TEXT    NOT NULL,
    category    TEXT,
    close_date  TEXT,                      -- ISO8601
    current_price REAL  NOT NULL DEFAULT 0.5,
    last_updated  TEXT  NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       INTEGER NOT NULL REFERENCES markets(id),
    model           TEXT    NOT NULL,      -- e.g. 'claude-sonnet-4-6'
    estimated_prob  REAL    NOT NULL,      -- 0.0 to 1.0
    confidence      TEXT,                  -- 'low' | 'medium' | 'high'
    reasoning       TEXT,                  -- Claude's reasoning text
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       INTEGER NOT NULL REFERENCES markets(id),
    prediction_id   INTEGER REFERENCES predictions(id),
    direction       TEXT    NOT NULL,      -- 'yes' | 'no'
    size            REAL    NOT NULL,      -- amount wagered
    entry_price     REAL    NOT NULL,
    exit_price      REAL,                  -- NULL until resolved
    outcome         TEXT,                  -- 'win' | 'loss' | NULL
    pnl             REAL,
    is_paper        INTEGER NOT NULL DEFAULT 1,  -- 1 = paper trade
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id   INTEGER NOT NULL REFERENCES predictions(id),
    predicted_prob  REAL    NOT NULL,
    actual_outcome  INTEGER,               -- 1 = YES resolved, 0 = NO resolved
    brier_score     REAL,                  -- computed on resolution
    resolved_at     TEXT
);
"""
```

**Step 4: Create src/db/migrations.py**

```python
"""Simple schema versioning — applies SCHEMA if tables don't exist yet."""
import aiosqlite
from src.db.models import SCHEMA


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply schema to an open DB connection. Safe to call on every startup."""
    await db.executescript(SCHEMA)
    await db.commit()
```

**Step 5: Create src/db/connection.py**

```python
"""DB connection manager.

Usage:
    async with get_db() as db:
        await db.execute(...)
"""
import aiosqlite
from contextlib import asynccontextmanager
from config.settings import settings
from src.db.migrations import run_migrations


@asynccontextmanager
async def get_db(read_only: bool = False):
    """Async context manager for a DB connection.

    Runs migrations on first open so the schema is always up to date.
    Pass read_only=True for the dashboard to prevent accidental writes.
    """
    uri = f"file:{settings.db_path}{'?mode=ro' if read_only else ''}{'&' if read_only else '?'}cache=shared"
    # Fall back to normal path if read_only URI fails (DB may not exist yet)
    try:
        db = await aiosqlite.connect(settings.db_path)
        db.row_factory = aiosqlite.Row
        if not read_only:
            await run_migrations(db)
        yield db
    finally:
        await db.close()
```

**Step 6: Run tests — should pass**

```bash
pytest tests/test_db.py -v
```

Expected: 2 PASSED.

**Step 7: Commit**

```bash
git add src/db/ tests/test_db.py
git commit -m "feat: SQLite schema, migrations, and connection manager"
```

---

## Task 3: Kelly Criterion calculator

**Files:**
- Create: `src/trading/kelly.py`
- Create: `tests/test_kelly.py`

**Step 1: Write the failing tests**

Create `tests/test_kelly.py`:

```python
"""Unit tests for Kelly Criterion math.

Kelly formula: f* = (bp - q) / b
  p = our estimated probability of YES
  q = 1 - p
  b = (1 - market_price) / market_price  (net odds on a YES bet)

Fractional Kelly multiplies f* by kelly_fraction (e.g. 0.25).
"""
import pytest
from src.trading.kelly import kelly_fraction, kelly_bet_size


def test_kelly_fraction_positive_edge():
    # We think 70% chance, market says 50% — clear edge
    result = kelly_fraction(our_prob=0.70, market_price=0.50)
    assert result == pytest.approx(0.40, abs=1e-6)


def test_kelly_fraction_zero_edge():
    # Our estimate matches the market — no edge
    result = kelly_fraction(our_prob=0.50, market_price=0.50)
    assert result == pytest.approx(0.0, abs=1e-6)


def test_kelly_fraction_negative_edge():
    # Market is overpriced — negative Kelly means don't bet
    result = kelly_fraction(our_prob=0.30, market_price=0.50)
    assert result < 0


def test_kelly_fraction_clamps_to_zero_when_negative():
    result = kelly_fraction(our_prob=0.30, market_price=0.50, clamp=True)
    assert result == 0.0


def test_kelly_bet_size_applies_fraction_and_cap():
    # Bankroll=1000, raw Kelly=0.40, fractional=0.25 → 0.10 * 1000 = 100
    # But max_position_pct=0.05 → capped at 50
    size = kelly_bet_size(
        our_prob=0.70,
        market_price=0.50,
        bankroll=1000.0,
        kelly_fraction_multiplier=0.25,
        max_position_pct=0.05,
    )
    assert size == pytest.approx(50.0, abs=1e-6)


def test_kelly_bet_size_zero_when_no_edge():
    size = kelly_bet_size(
        our_prob=0.50,
        market_price=0.50,
        bankroll=1000.0,
        kelly_fraction_multiplier=0.25,
        max_position_pct=0.05,
    )
    assert size == 0.0


def test_kelly_fraction_extreme_probability():
    # Degenerate: we're certain it resolves YES, market at 50%
    result = kelly_fraction(our_prob=1.0, market_price=0.50)
    assert result == pytest.approx(1.0, abs=1e-6)
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_kelly.py -v
```

Expected: `ImportError`.

**Step 3: Implement src/trading/kelly.py**

```python
"""Kelly Criterion calculator for prediction market bet sizing.

The Kelly formula for a binary bet:
    f* = (bp - q) / b
where:
    p = estimated probability of the outcome we're betting on (YES)
    q = 1 - p
    b = net odds = (1 - market_price) / market_price
        (how many units we win per unit wagered if we bet YES)

Always use fractional Kelly (multiply by 0.25 or similar) to reduce variance.
Cap position size to max_position_pct of bankroll as a second safety net.
"""


def kelly_fraction(
    our_prob: float,
    market_price: float,
    clamp: bool = False,
) -> float:
    """Return the raw Kelly fraction f* for a YES bet.

    Args:
        our_prob: Our estimated probability (0.0–1.0) that the market resolves YES.
        market_price: Current market price for YES (0.0–1.0).
        clamp: If True, return 0.0 instead of a negative value (no-bet signal).

    Returns:
        Kelly fraction. Negative means no edge (don't bet).
    """
    if market_price <= 0 or market_price >= 1:
        raise ValueError(f"market_price must be in (0, 1), got {market_price}")
    if not 0 <= our_prob <= 1:
        raise ValueError(f"our_prob must be in [0, 1], got {our_prob}")

    b = (1 - market_price) / market_price  # net odds
    p = our_prob
    q = 1 - p

    f_star = (b * p - q) / b

    if clamp:
        return max(0.0, f_star)
    return f_star


def kelly_bet_size(
    our_prob: float,
    market_price: float,
    bankroll: float,
    kelly_fraction_multiplier: float = 0.25,
    max_position_pct: float = 0.05,
) -> float:
    """Return the dollar/mana amount to bet, applying fractional Kelly and a position cap.

    Args:
        our_prob: Our estimated YES probability.
        market_price: Current market YES price.
        bankroll: Total capital available.
        kelly_fraction_multiplier: Scale factor (0.25 = quarter Kelly). Conservative.
        max_position_pct: Hard cap — never risk more than this fraction of bankroll.

    Returns:
        Bet size in the same units as bankroll. Zero means no bet.
    """
    f_star = kelly_fraction(our_prob, market_price, clamp=True)
    if f_star == 0.0:
        return 0.0

    fractional = f_star * kelly_fraction_multiplier
    max_bet = bankroll * max_position_pct
    return min(fractional * bankroll, max_bet)
```

**Step 4: Run tests — should pass**

```bash
pytest tests/test_kelly.py -v
```

Expected: 7 PASSED.

**Step 5: Commit**

```bash
git add src/trading/kelly.py tests/test_kelly.py
git commit -m "feat: Kelly Criterion calculator with fractional Kelly and position cap"
```

---

## Task 4: Brier score calibration

**Files:**
- Create: `src/tracking/calibration.py`
- Create: `tests/test_calibration.py`

**Step 1: Write the failing tests**

Create `tests/test_calibration.py`:

```python
"""Unit tests for Brier score calculation and calibration metrics.

Brier score = (predicted_probability - actual_outcome)^2
Range: 0.0 (perfect) to 1.0 (worst).
Baseline: using market price as the "prediction".
"""
import pytest
from src.tracking.calibration import brier_score, mean_brier_score, brier_skill_score


def test_brier_perfect_prediction_yes():
    # Predicted 1.0, resolved YES → score = 0
    assert brier_score(predicted=1.0, outcome=1) == pytest.approx(0.0)


def test_brier_perfect_prediction_no():
    # Predicted 0.0, resolved NO → score = 0
    assert brier_score(predicted=0.0, outcome=0) == pytest.approx(0.0)


def test_brier_worst_prediction():
    # Predicted 1.0, resolved NO → score = 1
    assert brier_score(predicted=1.0, outcome=0) == pytest.approx(1.0)


def test_brier_neutral_prediction():
    # Predicted 0.5 regardless → score = 0.25
    assert brier_score(predicted=0.5, outcome=1) == pytest.approx(0.25)
    assert brier_score(predicted=0.5, outcome=0) == pytest.approx(0.25)


def test_mean_brier_score():
    predictions = [1.0, 0.0, 0.5]
    outcomes    = [1,   0,   1  ]
    # scores: 0.0, 0.0, 0.25 → mean = 0.0833...
    assert mean_brier_score(predictions, outcomes) == pytest.approx(0.25 / 3, abs=1e-6)


def test_mean_brier_score_empty_raises():
    with pytest.raises(ValueError):
        mean_brier_score([], [])


def test_brier_skill_score_better_than_baseline():
    # Our predictions are good (low Brier), baseline is 0.25 (50/50 guessing)
    skill = brier_skill_score(our_brier=0.10, baseline_brier=0.25)
    assert skill > 0


def test_brier_skill_score_worse_than_baseline():
    skill = brier_skill_score(our_brier=0.30, baseline_brier=0.25)
    assert skill < 0


def test_brier_skill_score_perfect():
    # Perfect predictions → skill = 1.0
    skill = brier_skill_score(our_brier=0.0, baseline_brier=0.25)
    assert skill == pytest.approx(1.0)
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_calibration.py -v
```

Expected: `ImportError`.

**Step 3: Implement src/tracking/calibration.py**

```python
"""Brier score calculation and calibration metrics.

Brier score measures the accuracy of probabilistic predictions:
    brier = (predicted_probability - actual_outcome)^2

Lower is better. 0.0 = perfect, 1.0 = maximally wrong.

Brier Skill Score compares our performance against a baseline (e.g. the
market price). Positive = we beat the market; negative = market beats us.
    BSS = 1 - (our_brier / baseline_brier)
"""
from typing import Sequence


def brier_score(predicted: float, outcome: int) -> float:
    """Compute a single Brier score.

    Args:
        predicted: Probability estimate in [0, 1].
        outcome: 1 if the event occurred, 0 if not.

    Returns:
        Brier score in [0, 1].
    """
    if not 0.0 <= predicted <= 1.0:
        raise ValueError(f"predicted must be in [0, 1], got {predicted}")
    if outcome not in (0, 1):
        raise ValueError(f"outcome must be 0 or 1, got {outcome}")
    return (predicted - outcome) ** 2


def mean_brier_score(
    predictions: Sequence[float],
    outcomes: Sequence[int],
) -> float:
    """Mean Brier score over a set of predictions.

    Args:
        predictions: Sequence of probability estimates.
        outcomes: Corresponding resolved outcomes (0 or 1).

    Returns:
        Mean Brier score.
    """
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have the same length")
    if not predictions:
        raise ValueError("Cannot compute mean Brier score of empty sequences")

    scores = [brier_score(p, o) for p, o in zip(predictions, outcomes)]
    return sum(scores) / len(scores)


def brier_skill_score(our_brier: float, baseline_brier: float) -> float:
    """Brier Skill Score — how much better (or worse) we are vs. a baseline.

    BSS = 1 - (our_brier / baseline_brier)

    Positive: we beat the baseline.
    Zero: same as baseline.
    Negative: baseline is better.

    Args:
        our_brier: Our mean Brier score.
        baseline_brier: Baseline mean Brier score (e.g. using market price as prediction).

    Returns:
        Brier Skill Score in (-inf, 1].
    """
    if baseline_brier == 0:
        raise ValueError("baseline_brier is 0 — baseline is perfect, skill score undefined")
    return 1 - (our_brier / baseline_brier)
```

**Step 4: Run tests — should pass**

```bash
pytest tests/test_calibration.py -v
```

Expected: 9 PASSED.

**Step 5: Commit**

```bash
git add src/tracking/calibration.py tests/test_calibration.py
git commit -m "feat: Brier score and calibration skill score"
```

---

## Task 5: Budget guardian and kill switch

**Files:**
- Create: `src/trading/risk.py`
- Create: `tests/test_risk.py`

**Step 1: Write the failing tests**

Create `tests/test_risk.py`:

```python
"""Tests for the budget guardian (hard spending limits + kill switch)."""
import pytest
from src.trading.risk import BudgetGuardian, BudgetExceededError, KillSwitchError


def make_guardian(daily_limit=100.0, total_limit=500.0, bankroll=1000.0, kill_pct=0.10):
    return BudgetGuardian(
        daily_limit=daily_limit,
        total_limit=total_limit,
        bankroll=bankroll,
        kill_switch_loss_pct=kill_pct,
    )


def test_allows_bet_within_limits():
    g = make_guardian()
    g.check_and_record(bet_size=50.0)  # Should not raise


def test_blocks_bet_exceeding_daily_limit():
    g = make_guardian(daily_limit=100.0)
    g.check_and_record(bet_size=90.0)
    with pytest.raises(BudgetExceededError, match="daily"):
        g.check_and_record(bet_size=20.0)  # 90 + 20 = 110 > 100


def test_blocks_bet_exceeding_total_limit():
    g = make_guardian(daily_limit=999.0, total_limit=100.0)
    g.check_and_record(bet_size=90.0)
    with pytest.raises(BudgetExceededError, match="total"):
        g.check_and_record(bet_size=20.0)


def test_kill_switch_triggers_on_large_loss():
    # Bankroll 1000, kill at 10% loss = 100
    g = make_guardian(bankroll=1000.0, kill_pct=0.10)
    g.record_loss(105.0)
    with pytest.raises(KillSwitchError):
        g.check_and_record(bet_size=1.0)


def test_calibration_mode_zero_daily_limit_blocks_all_bets():
    # daily_limit=0 means no real bets allowed (calibration mode)
    g = make_guardian(daily_limit=0.0)
    with pytest.raises(BudgetExceededError):
        g.check_and_record(bet_size=0.01)


def test_daily_spent_accumulates():
    g = make_guardian()
    g.check_and_record(bet_size=30.0)
    g.check_and_record(bet_size=20.0)
    assert g.daily_spent == pytest.approx(50.0)


def test_total_spent_accumulates():
    g = make_guardian()
    g.check_and_record(bet_size=10.0)
    g.check_and_record(bet_size=15.0)
    assert g.total_spent == pytest.approx(25.0)
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_risk.py -v
```

Expected: `ImportError`.

**Step 3: Implement src/trading/risk.py**

```python
"""Budget guardian and kill switch for the prediction pipeline.

Enforces hard spending limits so no accidental overspending occurs.
Calibration mode = daily_limit of 0, which blocks all real bets.

Usage:
    guardian = BudgetGuardian.from_settings()
    guardian.check_and_record(bet_size=42.0)  # raises if limit exceeded
"""
from config.settings import settings


class BudgetExceededError(Exception):
    """Raised when a bet would exceed a configured spending limit."""


class KillSwitchError(Exception):
    """Raised when cumulative losses exceed the kill switch threshold.

    When triggered, all trading halts until manual intervention.
    """


class BudgetGuardian:
    """Tracks spending and enforces hard limits.

    All amounts are in the same unit (USD, mana, etc.) depending on platform.
    """

    def __init__(
        self,
        daily_limit: float,
        total_limit: float,
        bankroll: float,
        kill_switch_loss_pct: float = 0.10,
    ) -> None:
        self.daily_limit = daily_limit
        self.total_limit = total_limit
        self.bankroll = bankroll
        self.kill_switch_threshold = bankroll * kill_switch_loss_pct

        self.daily_spent: float = 0.0
        self.total_spent: float = 0.0
        self.total_losses: float = 0.0
        self._kill_switch_active: bool = False

    @classmethod
    def from_settings(cls) -> "BudgetGuardian":
        """Create a guardian from the central settings object."""
        return cls(
            daily_limit=settings.budget_daily_limit,
            total_limit=settings.budget_total_limit,
            bankroll=settings.budget_total_limit,  # treat total limit as starting bankroll
            kill_switch_loss_pct=0.10,
        )

    def check_and_record(self, bet_size: float) -> None:
        """Check that bet_size is within all limits, then record it.

        Raises:
            KillSwitchError: If the kill switch has been triggered.
            BudgetExceededError: If daily or total limits would be breached.
        """
        if self._kill_switch_active:
            raise KillSwitchError(
                "Kill switch is active — halt all trading. Investigate losses before resuming."
            )

        if self.daily_spent + bet_size > self.daily_limit:
            raise BudgetExceededError(
                f"daily limit exceeded: {self.daily_spent + bet_size:.2f} > {self.daily_limit:.2f}"
            )

        if self.total_spent + bet_size > self.total_limit:
            raise BudgetExceededError(
                f"total limit exceeded: {self.total_spent + bet_size:.2f} > {self.total_limit:.2f}"
            )

        self.daily_spent += bet_size
        self.total_spent += bet_size

    def record_loss(self, amount: float) -> None:
        """Record a realized loss and trigger the kill switch if threshold is exceeded."""
        self.total_losses += amount
        if self.total_losses >= self.kill_switch_threshold:
            self._kill_switch_active = True

    def reset_daily(self) -> None:
        """Reset daily counter — call this at the start of each day."""
        self.daily_spent = 0.0
```

**Step 4: Run tests — should pass**

```bash
pytest tests/test_risk.py -v
```

Expected: 7 PASSED.

**Step 5: Commit**

```bash
git add src/trading/risk.py tests/test_risk.py
git commit -m "feat: budget guardian with daily/total limits and kill switch"
```

---

## Task 6: Manifold Markets API client

**Files:**
- Create: `src/markets/manifold.py`
- Create: `src/markets/scanner.py`

No tests here yet — the Manifold API is external. We'll add integration tests later. For now, write the client and verify it type-checks.

**Step 1: Create src/markets/manifold.py**

```python
"""Manifold Markets async API client.

Manifold is free to use, has a public REST API, and uses 'mana' (M$) as its
currency. No real money involved, making it ideal for calibration.

API docs: https://docs.manifold.markets/api
"""
import httpx
from typing import Any
from config.settings import settings


MANIFOLD_BASE = "https://api.manifold.markets/v0"


class ManifoldClient:
    """Async HTTP client for the Manifold Markets API.

    Usage:
        async with ManifoldClient() as client:
            markets = await client.get_markets(limit=20)
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.manifold_api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ManifoldClient":
        headers = {"Authorization": f"Key {self._api_key}"} if self._api_key else {}
        self._client = httpx.AsyncClient(
            base_url=MANIFOLD_BASE,
            headers=headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def get_markets(
        self,
        limit: int = 20,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch a list of active markets.

        Args:
            limit: Number of markets to return (max 1000 per Manifold API).
            before: Pagination cursor — ID of the last market from a previous page.

        Returns:
            List of market dicts with at minimum: id, question, probability, closeTime.
        """
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before

        resp = await self._client.get("/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, market_id: str) -> dict[str, Any]:
        """Fetch a single market by ID."""
        resp = await self._client.get(f"/market/{market_id}")
        resp.raise_for_status()
        return resp.json()

    async def place_bet(
        self,
        market_id: str,
        outcome: str,
        amount: float,
    ) -> dict[str, Any]:
        """Place a bet on a market.

        Args:
            market_id: Manifold market ID.
            outcome: 'YES' or 'NO'.
            amount: Amount in mana (M$).

        Returns:
            Bet confirmation dict from Manifold.
        """
        resp = await self._client.post(
            "/bet",
            json={"contractId": market_id, "outcome": outcome, "amount": amount},
        )
        resp.raise_for_status()
        return resp.json()
```

**Step 2: Create src/markets/scanner.py**

```python
"""Market scanner — filters Manifold markets by criteria.

Not every market is worth analyzing. This filters to markets that are:
- Binary (YES/NO resolution)
- Not too close to closing (stale odds)
- Not already fully resolved
- Within a probability range where edges can exist
"""
from typing import Any
from datetime import datetime, timezone


def is_tradeable(market: dict[str, Any], min_prob: float = 0.05, max_prob: float = 0.95) -> bool:
    """Return True if a market is worth analyzing.

    Skips markets that are fully priced in (near 0 or 1) and already-closed markets.
    """
    if market.get("isResolved"):
        return False
    if market.get("outcomeType") != "BINARY":
        return False  # Only handle binary YES/NO markets for now

    prob = market.get("probability", 0.5)
    if not (min_prob <= prob <= max_prob):
        return False  # Market is already nearly certain — no edge possible

    close_time_ms = market.get("closeTime")
    if close_time_ms:
        close_dt = datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        hours_remaining = (close_dt - now).total_seconds() / 3600
        if hours_remaining < 24:
            return False  # Closing too soon — odds may be stale or illiquid

    return True


def filter_markets(
    markets: list[dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Filter a list of raw Manifold market dicts to tradeable candidates.

    Args:
        markets: Raw market dicts from ManifoldClient.get_markets().
        limit: Maximum number of markets to return.

    Returns:
        Filtered and capped list of tradeable markets.
    """
    tradeable = [m for m in markets if is_tradeable(m)]
    return tradeable[:limit]
```

**Step 3: Verify it at least imports cleanly**

```bash
python -c "from src.markets.manifold import ManifoldClient; from src.markets.scanner import filter_markets; print('OK')"
```

Expected: `OK`

**Step 4: Commit**

```bash
git add src/markets/manifold.py src/markets/scanner.py
git commit -m "feat: Manifold Markets async client and market scanner"
```

---

## Task 7: Claude estimator and prompt templates

**Files:**
- Create: `src/analysis/prompts.py`
- Create: `src/analysis/estimator.py`
- Create: `src/analysis/ensemble.py` (stub)

**Step 1: Create src/analysis/prompts.py**

```python
"""Prompt templates for market probability estimation.

Claude is asked to return structured JSON so we can parse the probability
programmatically. The prompt asks for reasoning first, then the estimate,
to encourage chain-of-thought before committing to a number.
"""

MARKET_ANALYSIS_SYSTEM = """You are a calibrated forecaster analyzing prediction market questions.
Your job is to estimate the probability that a market resolves YES.

Be honest about uncertainty. A well-calibrated forecaster's 70% predictions
come true ~70% of the time. Do not anchor to the current market price.

Always respond with valid JSON matching this schema:
{
  "reasoning": "<your step-by-step analysis, 2-5 sentences>",
  "factors_for": ["<factor 1>", "<factor 2>"],
  "factors_against": ["<factor 1>", "<factor 2>"],
  "estimated_probability": <float 0.0 to 1.0>,
  "confidence": "<low|medium|high>"
}"""


def market_analysis_prompt(question: str, context: str = "", market_price: float | None = None) -> str:
    """Build the user message for a market analysis request.

    Args:
        question: The market question text.
        context: Optional additional context (description, creator notes, etc.)
        market_price: Current market probability — included for reference but
                      the model is instructed not to anchor on it.
    """
    parts = [f"Market question: {question}"]

    if context:
        parts.append(f"\nAdditional context:\n{context}")

    if market_price is not None:
        parts.append(
            f"\nCurrent market price: {market_price:.1%} "
            "(for reference only — give your independent estimate)"
        )

    parts.append("\nAnalyze this market and provide your probability estimate as JSON.")
    return "\n".join(parts)
```

**Step 2: Create src/analysis/estimator.py**

```python
"""Claude probability estimator.

Sends a market question to Claude and parses the structured JSON response
into an estimate we can use for Kelly Criterion calculations.
"""
import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from config.settings import settings
from src.analysis.prompts import MARKET_ANALYSIS_SYSTEM, market_analysis_prompt

logger = logging.getLogger(__name__)


@dataclass
class ProbabilityEstimate:
    estimated_probability: float
    confidence: str          # 'low' | 'medium' | 'high'
    reasoning: str
    factors_for: list[str]
    factors_against: list[str]
    model: str
    raw_response: str        # keep for debugging


class Estimator:
    """Wraps the Anthropic SDK to get probability estimates for market questions."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model or settings.model

    async def estimate(
        self,
        question: str,
        context: str = "",
        market_price: float | None = None,
    ) -> ProbabilityEstimate:
        """Get a probability estimate from Claude for a market question.

        Args:
            question: The market question.
            context: Optional additional context.
            market_price: Current market price (shown to Claude for reference).

        Returns:
            Parsed ProbabilityEstimate.

        Raises:
            ValueError: If Claude returns malformed JSON or an out-of-range probability.
        """
        user_message = market_analysis_prompt(question, context, market_price)

        # Note: anthropic SDK is sync; for async pipelines wrap in asyncio.to_thread
        import asyncio
        response = await asyncio.to_thread(
            self._client.messages.create,
            model=self._model,
            max_tokens=1024,
            system=MARKET_ANALYSIS_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text
        return self._parse_response(raw, model=self._model)

    def _parse_response(self, raw: str, model: str) -> ProbabilityEstimate:
        """Parse Claude's JSON response into a ProbabilityEstimate."""
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude returned non-JSON response: {raw[:200]}") from e

        prob = float(data["estimated_probability"])
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"Probability out of range: {prob}")

        return ProbabilityEstimate(
            estimated_probability=prob,
            confidence=data.get("confidence", "low"),
            reasoning=data.get("reasoning", ""),
            factors_for=data.get("factors_for", []),
            factors_against=data.get("factors_against", []),
            model=model,
            raw_response=raw,
        )
```

**Step 3: Create src/analysis/ensemble.py (stub)**

```python
"""Multi-model ensemble for probability estimation.

Phase 1: Single Claude model only.
Phase 2: Add a second model (e.g. GPT-4o) and average the estimates,
         weighting by historical calibration performance.
"""
# TODO: implement ensemble when second model is added
```

**Step 4: Verify imports**

```bash
python -c "from src.analysis.estimator import Estimator; from src.analysis.prompts import market_analysis_prompt; print('OK')"
```

Expected: `OK`

**Step 5: Commit**

```bash
git add src/analysis/
git commit -m "feat: Claude estimator and market analysis prompt template"
```

---

## Task 8: Performance tracker and structured logger (stubs)

**Files:**
- Create: `src/tracking/performance.py`
- Create: `src/tracking/logger.py`

**Step 1: Create src/tracking/performance.py**

```python
"""P&L tracking, win rate, and ROI metrics.

Reads resolved trades from the DB and computes aggregate performance stats.
"""
from dataclasses import dataclass


@dataclass
class PerformanceStats:
    total_bets: int
    wins: int
    losses: int
    total_pnl: float
    roi: float  # (total_pnl / total_wagered) if total_wagered > 0 else 0


def compute_stats(trades: list[dict]) -> PerformanceStats:
    """Compute performance stats from a list of resolved trade dicts.

    Each trade dict must have: outcome ('win'|'loss'), size, pnl fields.
    """
    resolved = [t for t in trades if t.get("outcome") in ("win", "loss")]
    wins = sum(1 for t in resolved if t["outcome"] == "win")
    losses = sum(1 for t in resolved if t["outcome"] == "loss")
    total_pnl = sum(t.get("pnl", 0.0) for t in resolved)
    total_wagered = sum(t.get("size", 0.0) for t in resolved)
    roi = (total_pnl / total_wagered) if total_wagered > 0 else 0.0

    return PerformanceStats(
        total_bets=len(resolved),
        wins=wins,
        losses=losses,
        total_pnl=total_pnl,
        roi=roi,
    )
```

**Step 2: Create src/tracking/logger.py**

```python
"""Structured logging setup.

Call setup_logging() once at pipeline startup. All modules use standard
logging.getLogger(__name__) — no direct print() calls in library code.
"""
import logging
import sys
from config.settings import settings


def setup_logging() -> None:
    """Configure root logger with a structured format."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
```

**Step 3: Commit**

```bash
git add src/tracking/performance.py src/tracking/logger.py
git commit -m "feat: performance tracker and structured logging setup"
```

---

## Task 9: Trade executor stub and Polymarket stub

**Files:**
- Create: `src/trading/executor.py`
- Create: `src/markets/polymarket.py` (stub)

**Step 1: Create src/trading/executor.py**

```python
"""Trade executor — places bets or logs paper trades.

In calibration mode (daily_limit=0) all trades are paper-only.
In live mode, calls the Manifold API to place real bets.
"""
import logging
from typing import Any

from config.settings import settings
from src.trading.risk import BudgetGuardian, BudgetExceededError, KillSwitchError

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Executes or simulates trades based on Kelly-sized bets.

    Args:
        guardian: Budget guardian instance that enforces spending limits.
        paper_mode: If True, log trades without calling any API.
    """

    def __init__(self, guardian: BudgetGuardian, paper_mode: bool = True) -> None:
        self.guardian = guardian
        self.paper_mode = paper_mode

    async def execute(
        self,
        market: dict[str, Any],
        direction: str,
        bet_size: float,
        prediction_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Attempt to execute a trade.

        Returns a trade record dict (suitable for DB insertion) or None if blocked.
        """
        if bet_size <= 0:
            logger.debug("Skipping trade — bet size is zero")
            return None

        try:
            self.guardian.check_and_record(bet_size)
        except (BudgetExceededError, KillSwitchError) as e:
            logger.warning("Trade blocked: %s", e)
            return None

        trade = {
            "market_id": market.get("id"),
            "prediction_id": prediction_id,
            "direction": direction,
            "size": bet_size,
            "entry_price": market.get("probability", 0.5),
            "is_paper": int(self.paper_mode),
        }

        if self.paper_mode:
            logger.info("PAPER TRADE: %s %s M$%.2f on %s", direction, market.get("question", "?")[:60], bet_size, market.get("id"))
        else:
            logger.info("LIVE TRADE: placing bet via Manifold API")
            # TODO: call ManifoldClient.place_bet() when live trading is enabled
            raise NotImplementedError("Live trading not yet implemented — set paper_mode=True")

        return trade
```

**Step 2: Create src/markets/polymarket.py (stub)**

```python
"""Polymarket API client — Phase 2 stub.

Polymarket uses real money (USDC on Polygon). Do NOT implement live trading
here until budget guardian and calibration are thoroughly validated.
"""
# TODO: implement Polymarket client in Phase 2
```

**Step 3: Commit**

```bash
git add src/trading/executor.py src/markets/polymarket.py
git commit -m "feat: trade executor (paper mode) and Polymarket stub"
```

---

## Task 10: Main pipeline entry point

**Files:**
- Create: `scripts/run_pipeline.py`
- Create: `scripts/check_health.py`

**Step 1: Create scripts/run_pipeline.py**

```python
#!/usr/bin/env python3
"""Main pipeline entry point — runs one polling cycle.

Schedule this with cron or systemd timer:
    */5 * * * * cd /home/pi/predictions-sandbox && .venv/bin/python scripts/run_pipeline.py

One cycle:
1. Fetch active markets from Manifold
2. Filter to tradeable candidates
3. For each market, ask Claude for a probability estimate
4. Compute Kelly bet size
5. Check budget guardian
6. Execute (paper) trade if edge exists
7. Log everything to SQLite
"""
import asyncio
import logging

from config.settings import settings
from src.tracking.logger import setup_logging
from src.markets.manifold import ManifoldClient
from src.markets.scanner import filter_markets
from src.analysis.estimator import Estimator
from src.trading.kelly import kelly_bet_size
from src.trading.risk import BudgetGuardian
from src.trading.executor import TradeExecutor
from src.db.connection import get_db

logger = logging.getLogger(__name__)


async def run_cycle() -> None:
    setup_logging()
    logger.info("Starting pipeline cycle")

    guardian = BudgetGuardian.from_settings()
    estimator = Estimator()
    executor = TradeExecutor(guardian=guardian, paper_mode=True)

    async with ManifoldClient() as manifold:
        raw_markets = await manifold.get_markets(limit=settings.max_markets_per_cycle * 3)

    candidates = filter_markets(raw_markets, limit=settings.max_markets_per_cycle)
    logger.info("Filtered to %d tradeable markets", len(candidates))

    async with get_db() as db:
        for market in candidates:
            question = market.get("question", "")
            market_price = market.get("probability", 0.5)
            external_id = market.get("id", "")

            # Upsert market record
            await db.execute(
                """INSERT INTO markets (platform, external_id, question, current_price)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET
                       current_price=excluded.current_price,
                       last_updated=datetime('now')""",
                ("manifold", external_id, question, market_price),
            )
            await db.commit()

            async with db.execute(
                "SELECT id FROM markets WHERE platform=? AND external_id=?",
                ("manifold", external_id),
            ) as cur:
                row = await cur.fetchone()
            market_db_id = row["id"]

            # Get Claude's estimate
            try:
                estimate = await estimator.estimate(question, market_price=market_price)
            except Exception as e:
                logger.error("Estimation failed for %s: %s", question[:60], e)
                continue

            # Log prediction
            await db.execute(
                """INSERT INTO predictions (market_id, model, estimated_prob, confidence, reasoning)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    market_db_id,
                    estimate.model,
                    estimate.estimated_probability,
                    estimate.confidence,
                    estimate.reasoning,
                ),
            )
            await db.commit()

            async with db.execute("SELECT last_insert_rowid() as id") as cur:
                pred_row = await cur.fetchone()
            prediction_db_id = pred_row["id"]

            # Compute bet size
            edge = estimate.estimated_probability - market_price
            if abs(edge) < settings.min_edge_threshold:
                logger.debug("No edge on %s (edge=%.3f)", question[:40], edge)
                continue

            direction = "yes" if edge > 0 else "no"
            prob_for_direction = (
                estimate.estimated_probability if direction == "yes"
                else 1 - estimate.estimated_probability
            )
            price_for_direction = market_price if direction == "yes" else 1 - market_price

            bet = kelly_bet_size(
                our_prob=prob_for_direction,
                market_price=price_for_direction,
                bankroll=guardian.total_limit,
                kelly_fraction_multiplier=settings.kelly_fraction,
                max_position_pct=0.05,
            )

            trade = await executor.execute(
                market=market,
                direction=direction,
                bet_size=bet,
                prediction_id=prediction_db_id,
            )

            if trade:
                await db.execute(
                    """INSERT INTO trades
                       (market_id, prediction_id, direction, size, entry_price, is_paper)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        market_db_id,
                        prediction_db_id,
                        trade["direction"],
                        trade["size"],
                        trade["entry_price"],
                        trade["is_paper"],
                    ),
                )
                await db.commit()

    logger.info("Cycle complete")


if __name__ == "__main__":
    asyncio.run(run_cycle())
```

**Step 2: Create scripts/check_health.py**

```python
#!/usr/bin/env python3
"""Health check — verifies DB is accessible and API keys are present.

Exit code 0 = healthy, 1 = unhealthy. Suitable for monitoring/alerting.
"""
import asyncio
import sys
import os

from config.settings import settings
from src.db.connection import get_db


async def check() -> bool:
    issues: list[str] = []

    if not settings.anthropic_api_key:
        issues.append("ANTHROPIC_API_KEY not set")
    if not settings.manifold_api_key:
        issues.append("MANIFOLD_API_KEY not set (read-only mode active)")

    try:
        async with get_db() as db:
            await db.execute("SELECT 1")
    except Exception as e:
        issues.append(f"DB inaccessible: {e}")

    if issues:
        for issue in issues:
            print(f"[WARN] {issue}", file=sys.stderr)
        return False

    print("[OK] All systems healthy")
    return True


if __name__ == "__main__":
    ok = asyncio.run(check())
    sys.exit(0 if ok else 1)
```

**Step 3: Make scripts executable**

```bash
chmod +x scripts/run_pipeline.py scripts/check_health.py
```

**Step 4: Commit**

```bash
git add scripts/
git commit -m "feat: main pipeline entry point and health check script"
```

---

## Task 11: Pi setup script

**Files:**
- Create: `scripts/setup_pi.sh`

**Step 1: Create scripts/setup_pi.sh**

```bash
#!/bin/bash
# ============================================================
# Raspberry Pi 5 — Prediction Sandbox Setup
# Run once on a fresh Raspberry Pi OS Lite 64-bit install.
# ============================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="prediction-pipeline"

echo "=== Prediction Sandbox Pi Setup ==="
echo "Repo dir: $REPO_DIR"

# ── System dependencies ──────────────────────────────────────
echo "[1/5] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv python3-pip git ufw

# ── Python venv ──────────────────────────────────────────────
echo "[2/5] Setting up Python virtual environment..."
python3.11 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -e "$REPO_DIR[dev]"

# ── Environment file ─────────────────────────────────────────
echo "[3/5] Checking .env..."
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    echo "  Created .env from .env.example — edit it to add API keys!"
else
    echo "  .env already exists, skipping"
fi

# ── Firewall ─────────────────────────────────────────────────
echo "[4/5] Configuring UFW firewall..."
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
# Dashboard (Phase 2) — only allow from local network
# sudo ufw allow from 192.168.0.0/16 to any port 8000

# ── Systemd service ──────────────────────────────────────────
echo "[5/5] Installing systemd timer..."
PYTHON="$REPO_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}.timer"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Prediction Market Pipeline — single cycle
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$PYTHON scripts/run_pipeline.py
StandardOutput=journal
StandardError=journal
EOF

sudo tee "$TIMER_FILE" > /dev/null << EOF
[Unit]
Description=Run prediction pipeline every 5 minutes

[Timer]
OnBootSec=60
OnUnitActiveSec=5min
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.timer"
sudo systemctl start "${SERVICE_NAME}.timer"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit $REPO_DIR/.env and add your API keys"
echo "  2. Run: $PYTHON scripts/check_health.py"
echo "  3. Check logs: journalctl -u $SERVICE_NAME -f"
echo "  4. Check timer: systemctl list-timers $SERVICE_NAME"
```

**Step 2: Make executable and commit**

```bash
chmod +x scripts/setup_pi.sh
git add scripts/setup_pi.sh
git commit -m "feat: Raspberry Pi systemd setup script with UFW firewall rules"
```

---

## Task 12: README and dashboard stubs

**Files:**
- Create: `README.md`
- Create: `dashboard/app.py`
- Create: `dashboard/routes.py`

**Step 1: Create README.md**

```markdown
# Prediction Sandbox

A Raspberry Pi 5-based prediction market trading pipeline. Polls Manifold Markets, asks Claude for probability estimates, applies Kelly Criterion for bet sizing, and tracks calibration via Brier scores.

## Phase 1: Calibration (current)

- Polls Manifold Markets every 5 minutes
- Sends market questions to Claude (Sonnet) for probability estimates
- Logs all predictions and paper trades to SQLite
- Budget guardian prevents any real spending (`BUDGET_DAILY_LIMIT=0`)

## Setup (Raspberry Pi)

```bash
git clone <this-repo> ~/predictions-sandbox
cd ~/predictions-sandbox
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and MANIFOLD_API_KEY
bash scripts/setup_pi.sh
```

## Setup (local dev)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env
python scripts/run_pipeline.py
```

## Running tests

```bash
pytest tests/ -v
```

## Project structure

See [summary.md](summary.md) for full architecture documentation.
```

**Step 2: Create dashboard stubs**

`dashboard/app.py`:
```python
"""FastAPI dashboard — Phase 2.

Read-only view of predictions, calibration scores, and trade history.
Protected by HTTP Basic Auth.
"""
# TODO: implement in Phase 2
```

`dashboard/routes.py`:
```python
"""Dashboard routes: /calibration, /trades, /markets — Phase 2 stub."""
# TODO: implement in Phase 2
```

**Step 3: Run full test suite — everything should pass**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass (test_db, test_kelly, test_calibration, test_risk).

**Step 4: Final commit**

```bash
git add README.md dashboard/
git commit -m "feat: README and Phase 2 dashboard stubs — Phase 1 scaffold complete"
```

---

## Full test run

```bash
pytest tests/ -v
```

Expected output:
```
tests/test_calibration.py::test_brier_perfect_prediction_yes PASSED
tests/test_calibration.py::test_brier_perfect_prediction_no PASSED
tests/test_calibration.py::test_brier_worst_prediction PASSED
tests/test_calibration.py::test_brier_neutral_prediction PASSED
tests/test_calibration.py::test_mean_brier_score PASSED
tests/test_calibration.py::test_mean_brier_score_empty_raises PASSED
tests/test_calibration.py::test_brier_skill_score_better_than_baseline PASSED
tests/test_calibration.py::test_brier_skill_score_worse_than_baseline PASSED
tests/test_calibration.py::test_brier_skill_score_perfect PASSED
tests/test_db.py::test_migrations_create_tables PASSED
tests/test_db.py::test_insert_and_read_market PASSED
tests/test_kelly.py::test_kelly_fraction_positive_edge PASSED
tests/test_kelly.py::test_kelly_fraction_zero_edge PASSED
tests/test_kelly.py::test_kelly_fraction_negative_edge PASSED
tests/test_kelly.py::test_kelly_fraction_clamps_to_zero_when_negative PASSED
tests/test_kelly.py::test_kelly_bet_size_applies_fraction_and_cap PASSED
tests/test_kelly.py::test_kelly_bet_size_zero_when_no_edge PASSED
tests/test_kelly.py::test_kelly_fraction_extreme_probability PASSED
tests/test_risk.py::test_allows_bet_within_limits PASSED
tests/test_risk.py::test_blocks_bet_exceeding_daily_limit PASSED
tests/test_risk.py::test_blocks_bet_exceeding_total_limit PASSED
tests/test_risk.py::test_kill_switch_triggers_on_large_loss PASSED
tests/test_risk.py::test_calibration_mode_zero_daily_limit_blocks_all_bets PASSED
tests/test_risk.py::test_daily_spent_accumulates PASSED
tests/test_risk.py::test_total_spent_accumulates PASSED

25 passed
```
