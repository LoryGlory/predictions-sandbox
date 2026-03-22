"""Unit tests for backtest module (no API or Claude calls)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.backtesting.backtest import BacktestReport, BacktestResult, fetch_resolved_markets


def make_result(estimated: float, market_price: float, outcome: int) -> BacktestResult:
    return BacktestResult(
        market_id="test",
        question="Will X happen?",
        resolution="YES" if outcome else "NO",
        outcome=outcome,
        market_price=market_price,
        estimated_probability=estimated,
        confidence="medium",
        reasoning="test",
        model_brier=(estimated - outcome) ** 2,
        baseline_brier=(market_price - outcome) ** 2,
    )


def test_report_count():
    r = BacktestReport()
    r.results = [make_result(0.7, 0.5, 1), make_result(0.3, 0.5, 0)]
    assert r.count == 2


def test_report_mean_model_brier():
    r = BacktestReport()
    # perfect predictions → brier = 0
    r.results = [make_result(1.0, 0.5, 1), make_result(0.0, 0.5, 0)]
    assert r.mean_model_brier == pytest.approx(0.0)


def test_report_mean_baseline_brier():
    r = BacktestReport()
    # baseline is 0.5 always → brier = 0.25 each
    r.results = [make_result(1.0, 0.5, 1), make_result(0.0, 0.5, 0)]
    assert r.mean_baseline_brier == pytest.approx(0.25)


def test_report_skill_score_positive_when_model_beats_baseline():
    r = BacktestReport()
    r.results = [make_result(1.0, 0.5, 1)]  # perfect model, 0.25 baseline
    assert r.skill_score > 0


def test_report_skill_score_negative_when_baseline_beats_model():
    r = BacktestReport()
    r.results = [make_result(0.0, 0.9, 1)]  # model=1.0 brier, baseline=0.01
    assert r.skill_score < 0


def test_empty_report_returns_zero_scores():
    r = BacktestReport()
    assert r.count == 0
    assert r.mean_model_brier == pytest.approx(0.0)
    assert r.mean_baseline_brier == pytest.approx(0.0)
    assert r.skill_score == pytest.approx(0.0)


async def test_fetch_resolved_markets_filters_correctly():
    """fetch_resolved_markets should only return resolved binary YES/NO markets."""
    batch = [
        {"id": "1", "isResolved": True, "outcomeType": "BINARY", "resolution": "YES", "probability": 0.8},
        {"id": "2", "isResolved": False, "outcomeType": "BINARY", "resolution": None, "probability": 0.5},
        {"id": "3", "isResolved": True, "outcomeType": "MULTIPLE_CHOICE", "resolution": "YES", "probability": 0.6},
        {"id": "4", "isResolved": True, "outcomeType": "BINARY", "resolution": "NO", "probability": 0.2},
    ]
    mock_client = MagicMock()
    # First call returns the batch; second call returns empty to stop pagination.
    mock_client.get_markets = AsyncMock(side_effect=[batch, []])

    result = await fetch_resolved_markets(mock_client, count=10)
    assert len(result) == 2
    assert result[0]["id"] == "1"
    assert result[1]["id"] == "4"
