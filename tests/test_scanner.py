"""Unit tests for market scanner filter logic."""
import time
from unittest.mock import patch

from src.markets.scanner import (
    check_category,
    filter_markets,
    get_tags,
    is_polymarket_tradeable,
    is_tradeable,
    needs_realtime_search,
)


def binary_market(**kwargs):
    """Helper to build a minimal binary market dict.

    Includes a whitelisted category tag by default so markets pass category
    filtering. Override groupSlugs to test category logic specifically.
    """
    base = {
        "outcomeType": "BINARY",
        "isResolved": False,
        "probability": 0.5,
        "closeTime": int((time.time() + 7 * 24 * 3600) * 1000),  # 7 days from now
        "groupSlugs": ["fun"],  # whitelisted by default
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


def test_accepts_market_with_whitelisted_tags():
    assert is_tradeable(binary_market(
        question="Will AI pass the bar exam?", groupSlugs=["fun", "competitive-gaming"]
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


# ── Category whitelist/blacklist filter tests ────────────────────────────


def _mock_settings(**overrides):
    """Patch settings for category filter tests."""
    from config.settings import Settings
    defaults = {
        "category_filter_enabled": True,
        "anthropic_api_key": "",
        "manifold_api_key": "",
        "budget_daily_limit": 0,
        "budget_total_limit": 50,
        "kelly_fraction": 0.25,
        "min_edge_threshold": 0.05,
        "max_position_pct": 0.05,
        "model": "claude-sonnet-4-6",
        "poll_interval_seconds": 1800,
        "max_markets_per_cycle": 20,
        "log_level": "INFO",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "db_path": "predictions.db",
        "daily_api_budget": 3.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_category_filter_rejects_blacklisted():
    market = binary_market(groupSlugs=["metamarkets", "fun"])
    passes, reason = check_category(market)
    assert not passes
    assert "blacklisted" in reason


def test_category_filter_allows_untagged():
    """Untagged markets pass — Manifold list endpoint doesn't return tags."""
    market = binary_market(groupSlugs=[])
    passes, _ = check_category(market)
    assert passes


def test_category_filter_allows_unknown_category():
    """Unknown categories pass — only blacklisted ones are rejected."""
    market = binary_market(groupSlugs=["some-random-category"])
    passes, _ = check_category(market)
    assert passes


def test_category_filter_passes_whitelisted():
    market = binary_market(groupSlugs=["competitive-gaming", "fun"])
    passes, reason = check_category(market)
    assert passes
    assert reason == ""


# ── Whitelist mode tests ────────────────────────────────────────────────


def test_whitelist_mode_passes_whitelisted_market():
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=True)):
        market = binary_market(groupSlugs=["competitive-gaming"])
        passes, reason = check_category(market)
        assert passes
        assert reason == ""


def test_whitelist_mode_rejects_non_whitelisted_market():
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=True)):
        market = binary_market(groupSlugs=["random-tag"])
        passes, reason = check_category(market)
        assert not passes
        assert "whitelist mode" in reason


def test_whitelist_mode_rejects_untagged_market():
    """Whitelist mode is strict — no tags means no whitelist match."""
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=True)):
        market = binary_market(groupSlugs=[])
        passes, reason = check_category(market)
        assert not passes
        assert "no tags" in reason


def test_whitelist_mode_still_rejects_blacklisted():
    """Blacklist still wins over whitelist if both tags present."""
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=True)):
        market = binary_market(groupSlugs=["competitive-gaming", "metamarkets"])
        passes, reason = check_category(market)
        assert not passes
        assert "blacklisted" in reason


def test_whitelist_mode_off_allows_unrelated_tags():
    """Default behavior unchanged: non-whitelisted tags pass when whitelist mode is off."""
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=False)):
        market = binary_market(groupSlugs=["random-tag"])
        passes, _ = check_category(market)
        assert passes


def test_whitelist_mode_allows_untagged_with_flag():
    """allow_untagged=True lets untagged markets pass through bulk filter, to be
    re-checked after tag enrichment. Without it, all Manifold markets would be
    rejected pre-enrichment."""
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=True)):
        market = binary_market(groupSlugs=[])
        passes, _ = check_category(market, allow_untagged=True)
        assert passes


def test_whitelist_mode_blacklist_still_wins_with_allow_untagged():
    """allow_untagged shouldn't bypass blacklist enforcement."""
    with patch("src.markets.scanner.settings", _mock_settings(whitelist_mode=True)):
        market = binary_market(groupSlugs=["metamarkets"])
        passes, reason = check_category(market, allow_untagged=True)
        assert not passes
        assert "blacklisted" in reason


def test_category_filter_disabled_passes_all():
    with patch("src.markets.scanner.settings", _mock_settings(category_filter_enabled=False)):
        market = binary_market(groupSlugs=["metamarkets"])
        passes, _ = check_category(market)
        assert passes


def test_category_filter_disabled_passes_untagged():
    with patch("src.markets.scanner.settings", _mock_settings(category_filter_enabled=False)):
        market = binary_market()
        passes, _ = check_category(market)
        assert passes


def test_is_tradeable_respects_category_filter():
    """Whitelisted passes, blacklisted rejected, unknown allowed."""
    whitelisted = binary_market(
        question="Will I finish my goal?",
        groupSlugs=["personal-goals"],
    )
    assert is_tradeable(whitelisted) is True

    blacklisted = binary_market(
        question="Some cricket match?",
        groupSlugs=["cricket", "ipl-2026"],
    )
    assert is_tradeable(blacklisted) is False

    unknown = binary_market(
        question="Some random question?",
        groupSlugs=["obscure-category"],
    )
    assert is_tradeable(unknown) is True


# ── Polymarket filter tests ─────────────────────────────────────────────


def _poly_market(**kwargs):
    """Helper to build a minimal Polymarket market dict."""
    base = {
        "question": "Will X happen?",
        "outcomes": '["Yes", "No"]',  # JSON string, as Polymarket returns
        "outcomePrices": '["0.55", "0.45"]',
        "volume": "50000",
        "closed": False,
        "resolved": None,  # Polymarket uses null, not False
        "endDate": None,
    }
    base.update(kwargs)
    return base


def test_polymarket_accepts_valid_market():
    assert is_polymarket_tradeable(_poly_market()) is True


def test_polymarket_parses_json_string_outcomes():
    """Polymarket returns outcomes as JSON strings, not Python lists."""
    assert is_polymarket_tradeable(_poly_market(outcomes='["Yes", "No"]')) is True


def test_polymarket_rejects_non_binary_outcomes():
    assert is_polymarket_tradeable(_poly_market(outcomes='["A", "B", "C"]')) is False


def test_polymarket_rejects_resolved():
    assert is_polymarket_tradeable(_poly_market(resolved=True)) is False


def test_polymarket_allows_resolved_null():
    """Polymarket uses null (None) for unresolved markets."""
    assert is_polymarket_tradeable(_poly_market(resolved=None)) is True


def test_polymarket_rejects_low_volume():
    assert is_polymarket_tradeable(_poly_market(volume="500")) is False


def test_polymarket_rejects_invalid_outcomes_string():
    assert is_polymarket_tradeable(_poly_market(outcomes="not valid json")) is False


# ── needs_realtime_search tests ─────────────────────────────────────────


def test_realtime_search_triggered_by_iran_tag():
    assert needs_realtime_search(["iran", "politics"]) is True


def test_realtime_search_triggered_by_middle_east():
    assert needs_realtime_search(["middle-east"]) is True


def test_realtime_search_not_triggered_for_stable_categories():
    assert needs_realtime_search(["competitive-gaming", "esports"]) is False


def test_realtime_search_empty_tags():
    assert needs_realtime_search([]) is False
    assert needs_realtime_search(None) is False


# Keyword-based detection (fallback when tags are missing)


def test_realtime_search_keyword_iran_in_question():
    assert needs_realtime_search([], "Will Iran accept the ceasefire?") is True


def test_realtime_search_keyword_ceasefire_in_question():
    assert needs_realtime_search([], "Will the ceasefire hold past May?") is True


def test_realtime_search_keyword_case_insensitive():
    assert needs_realtime_search(None, "Will RUSSIA strike Kyiv again?") is True


def test_realtime_search_keyword_word_boundary():
    # "warehouse" contains "war" as substring but shouldn't trigger
    assert needs_realtime_search([], "Will the new warehouse open by June?") is False


def test_realtime_search_no_trigger_on_stable_question():
    assert needs_realtime_search([], "Will Sabrina Carpenter's video cross 20M views?") is False


def test_realtime_search_tags_take_precedence():
    # If tags match, question doesn't need to
    assert needs_realtime_search(["middle-east"], "Something unrelated") is True
