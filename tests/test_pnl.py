"""Unit tests for realised-P&L computation on settled binary-market trades."""
import pytest

from src.trading.pnl import compute_pnl, compute_pnl_from_shares


def test_yes_bet_resolves_yes_profit():
    """Bet M$10 at YES price 0.40, resolves YES → profit = 10 * (1/0.4 - 1) = 15."""
    assert compute_pnl("yes", entry_price=0.40, size=10.0, outcome=1) == pytest.approx(15.0)


def test_yes_bet_resolves_no_full_loss():
    """Bet M$10 YES, resolves NO → lose the entire stake."""
    assert compute_pnl("yes", entry_price=0.40, size=10.0, outcome=0) == pytest.approx(-10.0)


def test_no_bet_resolves_no_profit():
    """Bet M$10 NO at YES price 0.40 (NO price 0.60), resolves NO → 10 * (0.4/0.6) ≈ 6.667."""
    expected = 10.0 * (0.40 / 0.60)
    assert compute_pnl("no", entry_price=0.40, size=10.0, outcome=0) == pytest.approx(expected)


def test_no_bet_resolves_yes_full_loss():
    """Bet M$10 NO, resolves YES → lose the entire stake."""
    assert compute_pnl("no", entry_price=0.40, size=10.0, outcome=1) == pytest.approx(-10.0)


def test_direction_case_insensitive():
    """Direction comparison should be case-insensitive — Manifold sometimes uppercases it."""
    assert compute_pnl("YES", entry_price=0.50, size=10.0, outcome=1) == pytest.approx(10.0)
    assert compute_pnl("No", entry_price=0.50, size=10.0, outcome=0) == pytest.approx(10.0)


def test_breakeven_market_no_skew():
    """Bet at 50/50 market — profit equals stake on win, full loss on lose."""
    assert compute_pnl("yes", entry_price=0.50, size=5.0, outcome=1) == pytest.approx(5.0)
    assert compute_pnl("no", entry_price=0.50, size=5.0, outcome=0) == pytest.approx(5.0)


def test_extreme_longshot_huge_payout():
    """Bet at very low YES price → massive multiplier on win."""
    # YES at 0.01, win pays out 100x
    result = compute_pnl("yes", entry_price=0.01, size=1.0, outcome=1)
    assert result == pytest.approx(99.0)


def test_extreme_favourite_tiny_payout():
    """Bet at very high YES price → tiny multiplier on win."""
    # YES at 0.99, win pays out ~1.01x
    result = compute_pnl("yes", entry_price=0.99, size=100.0, outcome=1)
    assert result == pytest.approx(100.0 * (1 / 0.99 - 1))
    assert 0 < result < 2.0


def test_invalid_outcome_raises():
    with pytest.raises(ValueError, match="outcome"):
        compute_pnl("yes", entry_price=0.5, size=10.0, outcome=2)


def test_invalid_entry_price_zero_raises():
    """entry_price=0 would mean infinite payout — guard against bad data."""
    with pytest.raises(ValueError, match="entry_price"):
        compute_pnl("yes", entry_price=0.0, size=10.0, outcome=1)


def test_invalid_entry_price_one_raises():
    """entry_price=1 would mean zero payout — guard against bad data."""
    with pytest.raises(ValueError, match="entry_price"):
        compute_pnl("yes", entry_price=1.0, size=10.0, outcome=1)


def test_zero_size_zero_pnl():
    """A zero-size bet returns zero pnl regardless of outcome."""
    assert compute_pnl("yes", entry_price=0.5, size=0.0, outcome=1) == pytest.approx(0.0)
    assert compute_pnl("no", entry_price=0.5, size=0.0, outcome=0) == pytest.approx(0.0)


# ── Exact P&L from actual fill (shares) ─────────────────────────────────


def test_shares_pnl_win_is_shares_minus_stake():
    """M$5 bought 9.2 YES shares; resolves YES → profit 9.2 - 5 = 4.2."""
    assert compute_pnl_from_shares("yes", shares=9.2, size=5.0, outcome=1) == pytest.approx(4.2)


def test_shares_pnl_loss_is_full_stake():
    assert compute_pnl_from_shares("yes", shares=9.2, size=5.0, outcome=0) == pytest.approx(-5.0)


def test_shares_pnl_no_side_win():
    assert compute_pnl_from_shares("no", shares=7.5, size=5.0, outcome=0) == pytest.approx(2.5)


def test_shares_pnl_less_optimistic_than_entry_price_approximation():
    """The whole point: a CPMM fill averages worse than the pre-bet price.
    Exact shares-based profit must come out below the approximation."""
    # Pre-bet YES price 0.50 → approximation assumes 5/0.5 = 10 shares.
    approx = compute_pnl("yes", entry_price=0.50, size=5.0, outcome=1)
    # The bet itself moved the price, so the actual fill bought fewer.
    exact = compute_pnl_from_shares("yes", shares=9.4, size=5.0, outcome=1)
    assert exact < approx


def test_shares_pnl_invalid_outcome_raises():
    with pytest.raises(ValueError, match="outcome"):
        compute_pnl_from_shares("yes", shares=5.0, size=5.0, outcome=2)


def test_shares_pnl_negative_shares_raises():
    with pytest.raises(ValueError, match="shares"):
        compute_pnl_from_shares("yes", shares=-1.0, size=5.0, outcome=1)
