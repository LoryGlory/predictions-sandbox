"""Unit tests for Kelly Criterion math.

Kelly formula: f* = (bp - q) / b
  p = our estimated probability of YES
  q = 1 - p
  b = net odds = (1 - market_price) / market_price

Fractional Kelly multiplies f* by kelly_fraction (e.g. 0.25).
"""
import pytest

from src.trading.kelly import kelly_bet_size, kelly_fraction


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


def test_kelly_fraction_raises_on_invalid_market_price():
    with pytest.raises(ValueError):
        kelly_fraction(our_prob=0.5, market_price=0.0)
    with pytest.raises(ValueError):
        kelly_fraction(our_prob=0.5, market_price=1.0)


def test_kelly_fraction_raises_on_invalid_prob():
    with pytest.raises(ValueError):
        kelly_fraction(our_prob=1.5, market_price=0.5)
    with pytest.raises(ValueError):
        kelly_fraction(our_prob=-0.1, market_price=0.5)


def test_kelly_fraction_non_symmetric_market():
    # our_prob=0.80, market_price=0.60
    # b = (1-0.60)/0.60 = 0.6667
    # f* = (0.6667*0.80 - 0.20) / 0.6667 = (0.5333 - 0.20) / 0.6667 = 0.3333/0.6667 = 0.50
    result = kelly_fraction(our_prob=0.80, market_price=0.60)
    assert result == pytest.approx(0.50, abs=1e-4)
