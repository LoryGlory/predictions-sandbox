"""Tests for the Phase −1 pilot's extraction query.

The pilot could not be validated against real data locally (the live corpus
lives on the Pi; the April backup predates market_price being populated), so
its SQL is exercised here against a synthetic database built from the CURRENT
schema. If the schema drifts, these fail rather than the pilot silently
returning zero rows.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from src.db.models import SCHEMA

_PILOT = Path(__file__).resolve().parent.parent / "scripts" / "longshot_pilot.py"
_spec = importlib.util.spec_from_file_location("longshot_pilot", _PILOT)
pilot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pilot)


def _make_db(path: Path, rows: list[dict]) -> Path:
    """Build a database on the real schema.

    Each row: market_price(s) for one market plus its resolved outcome.
    `prices` is a list so re-estimation of the same market can be simulated.
    """
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for i, row in enumerate(rows):
        conn.execute(
            "INSERT INTO markets (id, platform, external_id, question, current_price) "
            "VALUES (?, 'manifold', ?, ?, 0.5)",
            (i + 1, f"ext{i}", row.get("question", f"Market {i}")),
        )
        for price in row["prices"]:
            cur = conn.execute(
                "INSERT INTO predictions (market_id, model, estimated_prob, market_price) "
                "VALUES (?, 'test', 0.5, ?)",
                (i + 1, price),
            )
            if row["outcome"] is not None:
                conn.execute(
                    "INSERT INTO calibration "
                    "(prediction_id, predicted_prob, actual_outcome, brier_score) "
                    "VALUES (?, 0.5, ?, 0.25)",
                    (cur.lastrowid, row["outcome"]),
                )
    conn.commit()
    conn.close()
    return path


def test_extracts_price_and_outcome(tmp_path):
    db = _make_db(tmp_path / "t.db", [
        {"prices": [0.08], "outcome": 0},
        {"prices": [0.62], "outcome": 1},
    ])
    obs = pilot.load_observations(db)
    assert sorted(obs) == [(0.08, 0), (0.62, 1)]


def test_one_observation_per_market_despite_re_estimation(tmp_path):
    """The pipeline re-estimated the same market up to 3x/day. Counting each
    as an independent trial would inflate N and shrink the intervals — the
    same statistical error as the category claims, in a new place."""
    db = _make_db(tmp_path / "t.db", [
        {"prices": [0.10, 0.12, 0.15], "outcome": 0},
    ])
    obs = pilot.load_observations(db)
    assert len(obs) == 1
    assert obs[0][0] == pytest.approx(0.10)  # the FIRST estimate is kept


def test_unresolved_markets_excluded(tmp_path):
    db = _make_db(tmp_path / "t.db", [
        {"prices": [0.30], "outcome": None},   # still open
        {"prices": [0.40], "outcome": 1},
    ])
    assert pilot.load_observations(db) == [(0.40, 1)]


def test_degenerate_prices_excluded(tmp_path):
    """0.0 and 1.0 carry no information about a traded belief here and would
    distort the extreme buckets."""
    db = _make_db(tmp_path / "t.db", [
        {"prices": [0.0], "outcome": 0},
        {"prices": [1.0], "outcome": 1},
        {"prices": [0.45], "outcome": 1},
    ])
    assert pilot.load_observations(db) == [(0.45, 1)]


def test_diagnose_reports_zero_join_on_priceless_predictions(tmp_path):
    """Reproduces the April-backup case: resolved calibrations exist, but the
    predictions they point at have no market_price."""
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO markets (id, platform, external_id, question, current_price) "
        "VALUES (1, 'manifold', 'e', 'q', 0.5)"
    )
    conn.execute(
        "INSERT INTO predictions (id, market_id, model, estimated_prob, market_price) "
        "VALUES (1, 1, 'test', 0.5, NULL)"
    )
    conn.execute(
        "INSERT INTO calibration (prediction_id, predicted_prob, actual_outcome, "
        "brier_score) VALUES (1, 0.5, 1, 0.25)"
    )
    conn.commit()
    conn.close()

    counts = dict(pilot._diagnose(path))
    assert counts["resolved calibrations"] == 1
    assert counts["  joined to a priced prediction"] == 0
    assert pilot.load_observations(path) == []
