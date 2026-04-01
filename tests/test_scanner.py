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


# ── Low-signal filter tests ──────────────────────────────────────────────


def test_rejects_coin_flip():
    assert is_tradeable(binary_market(question="Daily Coin Flip")) is False


def test_rejects_daily_coinflip_variant():
    assert is_tradeable(binary_market(question="Daily Coinflip")) is False


def test_rejects_heads_or_tails():
    assert is_tradeable(binary_market(question="heads or tails")) is False


def test_rejects_tails_or_heads():
    assert is_tradeable(binary_market(question="tails or heads")) is False


def test_rejects_daily_market():
    assert is_tradeable(binary_market(question="Daily market")) is False


def test_rejects_simulation_match():
    assert is_tradeable(binary_market(question="Will P1 win ALS Tennis Simulation Match #002?")) is False


def test_rejects_non_english():
    assert is_tradeable(binary_market(question="האם נבון יהיה ה ס?")) is False


def test_accepts_english_analysis_question():
    assert is_tradeable(binary_market(question="Will the S&P 500 close above 5000 by end of Q2?")) is True


def test_accepts_politics_question():
    assert is_tradeable(binary_market(question="Will Biden win the 2024 election?")) is True


# ── No-edge category filter tests ────────────────────────────────────────


def test_rejects_cricket_market():
    assert is_tradeable(binary_market(
        question="Will CSK beat RR?", groupSlugs=["cricket", "ipl-2026"]
    )) is False


def test_rejects_crypto_speculation():
    assert is_tradeable(binary_market(
        question="Will Bitcoin hit $100k?", groupSlugs=["crypto-speculation"]
    )) is False


def test_rejects_sports_betting():
    assert is_tradeable(binary_market(
        question="Will Arsenal win?", groupSlugs=["football", "sports-betting"]
    )) is False


def test_accepts_market_without_no_edge_tags():
    assert is_tradeable(binary_market(
        question="Will AI pass the bar exam?", groupSlugs=["technology", "ai"]
    )) is True


# ── Tag extraction tests ─────────────────────────────────────────────────


def test_get_tags_from_group_slugs():
    market = {"groupSlugs": ["politics", "us-elections"], "outcomeType": "BINARY"}
    assert get_tags(market) == ["politics", "us-elections"]


def test_get_tags_fallback_to_tags_field():
    market = {"tags": ["science"], "outcomeType": "BINARY"}
    assert get_tags(market) == ["science"]


def test_get_tags_empty_when_none():
    market = {"outcomeType": "BINARY"}
    assert get_tags(market) == []
