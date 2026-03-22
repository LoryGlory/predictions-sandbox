"""Unit tests for Brier score calculation and calibration metrics.

Brier score = (predicted_probability - actual_outcome)^2
Range: 0.0 (perfect) to 1.0 (worst).
Baseline: using market price as the "prediction".
"""
import pytest

from src.tracking.calibration import (
    brier_score,
    brier_skill_score,
    mean_brier_score,
)


def test_brier_perfect_prediction_yes():
    assert brier_score(predicted=1.0, outcome=1) == pytest.approx(0.0)


def test_brier_perfect_prediction_no():
    assert brier_score(predicted=0.0, outcome=0) == pytest.approx(0.0)


def test_brier_worst_prediction():
    assert brier_score(predicted=1.0, outcome=0) == pytest.approx(1.0)


def test_brier_neutral_prediction():
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


def test_mean_brier_score_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        mean_brier_score([0.5, 0.6], [1])


def test_brier_skill_score_better_than_baseline():
    skill = brier_skill_score(our_brier=0.10, baseline_brier=0.25)
    assert skill > 0


def test_brier_skill_score_worse_than_baseline():
    skill = brier_skill_score(our_brier=0.30, baseline_brier=0.25)
    assert skill < 0


def test_brier_skill_score_perfect():
    skill = brier_skill_score(our_brier=0.0, baseline_brier=0.25)
    assert skill == pytest.approx(1.0)


def test_brier_skill_score_zero_baseline_raises():
    with pytest.raises(ValueError):
        brier_skill_score(our_brier=0.1, baseline_brier=0.0)


def test_brier_score_invalid_predicted_raises():
    with pytest.raises(ValueError):
        brier_score(predicted=1.5, outcome=1)
    with pytest.raises(ValueError):
        brier_score(predicted=-0.1, outcome=0)


def test_brier_score_invalid_outcome_raises():
    with pytest.raises(ValueError):
        brier_score(predicted=0.5, outcome=2)
