"""Brier score calculation and calibration metrics.

Brier score measures the accuracy of probabilistic predictions:
    brier = (predicted_probability - actual_outcome)^2

Lower is better. 0.0 = perfect, 1.0 = maximally wrong.

Brier Skill Score compares our performance against a baseline (e.g. the
market price). Positive = we beat the market; negative = market beats us.
    BSS = 1 - (our_brier / baseline_brier)
"""
from collections.abc import Sequence


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

    scores = [brier_score(p, o) for p, o in zip(predictions, outcomes, strict=True)]
    return sum(scores) / len(scores)


def brier_skill_score(our_brier: float, baseline_brier: float) -> float:
    """Brier Skill Score — how much better (or worse) we are vs. a baseline.

    BSS = 1 - (our_brier / baseline_brier)

    Args:
        our_brier: Our mean Brier score.
        baseline_brier: Baseline mean Brier score (e.g. using market price as prediction).

    Returns:
        Brier Skill Score in (-inf, 1].
    """
    if baseline_brier == 0:
        raise ValueError("baseline_brier is 0 — baseline is perfect, skill score undefined")
    return 1 - (our_brier / baseline_brier)
