"""Tests for the budget guardian (hard spending limits + kill switch)."""
import pytest

from src.trading.risk import BudgetExceededError, BudgetGuardian, KillSwitchError


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
    g = make_guardian(bankroll=1000.0, kill_pct=0.10)
    g.record_loss(105.0)
    with pytest.raises(KillSwitchError):
        g.check_and_record(bet_size=1.0)


def test_calibration_mode_zero_daily_limit_blocks_all_bets():
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


def test_reset_daily_clears_daily_spent():
    g = make_guardian()
    g.check_and_record(bet_size=50.0)
    g.reset_daily()
    assert g.daily_spent == 0.0
    g.check_and_record(bet_size=50.0)  # Should succeed after reset


def test_kill_switch_not_triggered_below_threshold():
    g = make_guardian(bankroll=1000.0, kill_pct=0.10)
    g.record_loss(99.0)  # Just below 10% of 1000
    g.check_and_record(bet_size=1.0)  # Should not raise
