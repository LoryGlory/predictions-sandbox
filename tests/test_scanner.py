"""Unit tests for market scanner filter logic."""
import time

from src.markets.scanner import filter_markets, get_tags, is_tradeable


def binary_market(**kwargs):
    """Helper to build a minimal binary market dict."""
    base = {
        "outcomeType": "BINARY",
        "isResolved": False,
        "probability": 0.5,
        "closeTime": int((time.time() + 7 * 24 * 3600) * 1000),  # 7 days from now
    }
    base.update(kwargs)
    return base


def test_passes_standard_binary_market():
    assert is_tradeable(binary_market()) is True


def test_rejects_resolved_market():
    assert is_tradeable(binary_market(isResolved=True)) is False


def test_rejects_non_binary_market():
    assert is_tradeable(binary_market(outcomeType="MULTIPLE_CHOICE")) is False


def test_rejects_probability_too_high():
    assert is_tradeable(binary_market(probability=0.96)) is False


def test_rejects_probability_too_low():
    assert is_tradeable(binary_market(probability=0.04)) is False


def test_rejects_closing_within_24h():
    close_soon = int((time.time() + 12 * 3600) * 1000)  # 12 hours from now
    assert is_tradeable(binary_market(closeTime=close_soon)) is False


def test_accepts_no_close_time():
    assert is_tradeable(binary_market(closeTime=None)) is True


def test_filter_markets_caps_results():
    markets = [binary_market() for _ in range(30)]
    result = filter_markets(markets, limit=5)
    assert len(result) == 5


def test_filter_markets_excludes_non_tradeable():
    markets = [
        binary_market(),
        binary_market(isResolved=True),
        binary_market(outcomeType="MULTIPLE_CHOICE"),
        binary_market(),
    ]
    result = filter_markets(markets)
    assert len(result) == 2


def test_get_tags_from_group_slugs():
    market = {"groupSlugs": ["politics", "us-elections"], "outcomeType": "BINARY"}
    assert get_tags(market) == ["politics", "us-elections"]


def test_get_tags_fallback_to_tags_field():
    market = {"tags": ["science"], "outcomeType": "BINARY"}
    assert get_tags(market) == ["science"]


def test_get_tags_empty_when_none():
    market = {"outcomeType": "BINARY"}
    assert get_tags(market) == []
