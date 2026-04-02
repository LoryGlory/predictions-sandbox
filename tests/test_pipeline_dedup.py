"""Tests for pipeline dedup logic — verifies skip conditions."""
from datetime import datetime, timedelta, timezone


def make_pred(hours_ago: float, market_price: float) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"timestamp": ts, "market_price": market_price}


def should_skip(last_pred: dict | None, market_price: float, max_age_hours: float = 4.0, max_price_move: float = 0.03) -> bool:
    """Extracted skip logic for testing — mirrors run_pipeline.py."""
    if last_pred is None or last_pred.get("market_price") is None:
        return False
    hours_since = (
        datetime.now(timezone.utc) -
        datetime.fromisoformat(last_pred["timestamp"])
    ).total_seconds() / 3600
    price_moved = abs(market_price - last_pred["market_price"])
    return hours_since < max_age_hours and price_moved < max_price_move


def test_skip_when_recent_and_price_stable():
    pred = make_pred(hours_ago=1.0, market_price=0.65)
    assert should_skip(pred, market_price=0.66) is True  # 1h ago, moved 1pp


def test_no_skip_when_old():
    pred = make_pred(hours_ago=5.0, market_price=0.65)
    assert should_skip(pred, market_price=0.65) is False  # 5h ago — stale


def test_no_skip_when_price_moved():
    pred = make_pred(hours_ago=1.0, market_price=0.65)
    assert should_skip(pred, market_price=0.70) is False  # moved 5pp


def test_no_skip_when_no_prior_prediction():
    assert should_skip(None, market_price=0.5) is False


def test_skip_boundary_exactly_3pp():
    pred = make_pred(hours_ago=1.0, market_price=0.65)
    assert should_skip(pred, market_price=0.68) is False  # exactly 3pp = not skipped (< not <=)


def test_no_skip_when_just_over_4h():
    pred = make_pred(hours_ago=4.01, market_price=0.65)
    assert should_skip(pred, market_price=0.65) is False


def test_no_skip_when_market_price_is_none():
    """Old predictions without stored market_price should not skip."""
    pred = {"timestamp": datetime.now(timezone.utc).isoformat(), "market_price": None}
    assert should_skip(pred, market_price=0.5) is False
