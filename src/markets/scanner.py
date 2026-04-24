"""Market scanner — filters Manifold markets by criteria.

Not every market is worth analyzing. This filters to markets that are:
- Binary (YES/NO resolution)
- Not too close to closing (stale odds)
- Not already fully resolved
- Within a probability range where edges can exist
- Not low-signal (coin flips, simulations, non-English)
- In a whitelisted category (when category filtering is enabled)
"""
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from config.settings import CATEGORY_BLACKLIST, CATEGORY_WHITELIST, REALTIME_TAGS, settings

logger = logging.getLogger(__name__)

# Markets matching these patterns are pure noise — Claude has no informational edge.
# Derived from backtest analysis showing 0.25 Brier (coin flip) on these categories.
_LOW_SIGNAL_PATTERNS = [
    re.compile(r"coin\s*flip", re.IGNORECASE),
    re.compile(r"daily\s*(coinflip|market|random)", re.IGNORECASE),
    re.compile(r"heads\s+or\s+tails|tails\s+or\s+heads", re.IGNORECASE),
    re.compile(r"simulation\s+match", re.IGNORECASE),
    re.compile(r"ALS\s+Tennis", re.IGNORECASE),
]

_BLACKLIST_SET = frozenset(CATEGORY_BLACKLIST)
_WHITELIST_SET = frozenset(CATEGORY_WHITELIST)
_REALTIME_SET = frozenset(REALTIME_TAGS)

# Keywords in the question text that suggest real-time information is needed.
# Compiled as word-boundary regex so "war" matches " war " but not "warehouse".
# Kept conservative — Claude's native reasoning is fine for most topics, and
# each match costs a web_search API call.
_REALTIME_KEYWORDS = [
    # Active conflicts / foreign policy
    "iran", "israel", "ukraine", "russia", "gaza", "palestine", "yemen",
    "hamas", "hezbollah", "taiwan",
    # Conflict-specific terms
    "ceasefire", "blockade", "invasion", "airstrike", "missile strike",
    # Breaking-event indicators in question text
    "today", "tonight", "this week",
]
_REALTIME_KEYWORDS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _REALTIME_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def needs_realtime_search(
    tags: list[str] | None,
    question: str | None = None,
) -> bool:
    """Return True if a market needs real-time information to answer well.

    Used to decide whether to enable Claude's web_search tool. Checks two signals:
      1. Market tags overlap with REALTIME_TAGS (reliable when tags are present)
      2. Question text contains keywords about active conflicts or time-sensitive
         events (fallback for Manifold, which doesn't return tags from its list
         endpoint)

    Either signal triggers search. Keep the keyword list conservative — each
    match costs a web_search API call.
    """
    if tags and (set(tags) & _REALTIME_SET):
        return True
    if question and _REALTIME_KEYWORDS_RE.search(question):
        return True
    return False

# Non-ASCII-heavy titles signal non-English markets where Claude's reasoning degrades
_MIN_ASCII_RATIO = 0.5


def is_low_signal(question: str) -> bool:
    """Return True if the market question is unlikely to benefit from Claude analysis.

    Catches: coin flips, daily randoms, simulations, non-English text.
    """
    for pattern in _LOW_SIGNAL_PATTERNS:
        if pattern.search(question):
            return True

    # Non-English check: if less than half the characters are ASCII letters/digits,
    # Claude's reasoning quality drops significantly
    if question:
        ascii_chars = sum(1 for c in question if c.isascii() and c.isalnum())
        total_alnum = sum(1 for c in question if c.isalnum())
        if total_alnum > 0 and (ascii_chars / total_alnum) < _MIN_ASCII_RATIO:
            return True

    return False


def check_category(market: dict[str, Any]) -> tuple[bool, str]:
    """Check whether a market passes category filtering.

    Returns (passes, reason) — reason is non-empty when the market is rejected.
    When category filtering is disabled, all markets pass.
    """
    if not settings.category_filter_enabled:
        return True, ""

    tags = set(get_tags(market))

    # Blacklisted categories are always rejected
    overlap = tags & _BLACKLIST_SET
    if overlap:
        return False, f"blacklisted category: {', '.join(sorted(overlap))}"

    # Untagged markets are allowed through — Manifold's list endpoint
    # doesn't return groupSlugs, so most markets arrive without tags.
    # The blacklist above catches known-bad categories when tags exist.
    if not tags:
        return True, ""

    # If tags exist, prefer whitelisted categories but don't reject
    # non-whitelisted ones — they might just be categories we haven't
    # evaluated yet.
    return True, ""


def is_tradeable(market: dict[str, Any], min_prob: float = 0.05, max_prob: float = 0.95) -> bool:
    """Return True if a market is worth analyzing.

    Skips markets that are fully priced in (near 0 or 1), already-closed,
    low-signal, or outside allowed categories.
    """
    if market.get("isResolved"):
        return False
    if market.get("outcomeType") != "BINARY":
        return False

    question = market.get("question", "")
    if is_low_signal(question):
        return False

    passes, reason = check_category(market)
    if not passes:
        logger.debug("Skipping '%s': %s", question[:60], reason)
        return False

    prob = market.get("probability", 0.5)
    if not (min_prob <= prob <= max_prob):
        return False

    close_time_ms = market.get("closeTime")
    if close_time_ms:
        close_dt = datetime.fromtimestamp(close_time_ms / 1000, tz=UTC)
        now = datetime.now(tz=UTC)
        hours_remaining = (close_dt - now).total_seconds() / 3600
        if hours_remaining < 24:
            return False

    return True


def filter_markets(
    markets: list[dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Filter a list of raw Manifold market dicts to tradeable candidates.

    Args:
        markets: Raw market dicts from ManifoldClient.get_markets().
        limit: Maximum number of markets to return.

    Returns:
        Filtered and capped list of tradeable markets.
    """
    tradeable = [m for m in markets if is_tradeable(m)]
    return tradeable[:limit]


def get_tags(market: dict[str, Any]) -> list[str]:
    """Extract tags from a Manifold market dict.

    Checks 'groupSlugs' first (newer API), falls back to 'tags'.
    """
    return market.get("groupSlugs") or market.get("tags") or []


# ── Polymarket-specific filtering ────────────────────────────────────────


def is_polymarket_tradeable(market: dict[str, Any]) -> bool:
    """Return True if a Polymarket market is worth paper-trading.

    Filters for:
    - Binary markets only (YES/NO outcomes)
    - Sufficient liquidity (volume > min threshold)
    - Not closing within 1 hour
    - Not already resolved
    - English title
    """
    # Active and not resolved — Polymarket uses null for unresolved
    if market.get("closed") or market.get("resolved") is True:
        return False

    # Binary only — Polymarket returns outcomes as a JSON string e.g. '["Yes", "No"]'
    outcomes = market.get("outcomes", [])
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            return False
    if len(outcomes) != 2:
        return False

    # Volume check
    volume = float(market.get("volume", 0) or 0)
    if volume < settings.polymarket_min_volume:
        return False

    # Closing soon check (within 1 hour)
    end_date = market.get("endDate") or market.get("end_date_iso")
    if end_date:
        try:
            close_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            now = datetime.now(tz=UTC)
            hours_remaining = (close_dt - now).total_seconds() / 3600
            if hours_remaining < 1:
                return False
        except (ValueError, TypeError):
            pass

    # Non-English check
    question = market.get("question", "")
    if is_low_signal(question):
        return False

    return True


def filter_polymarket_markets(
    markets: list[dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Filter Polymarket markets to paper-tradeable candidates."""
    tradeable = [m for m in markets if is_polymarket_tradeable(m)]
    return tradeable[:limit]
