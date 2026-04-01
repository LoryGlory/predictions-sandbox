"""Market scanner — filters Manifold markets by criteria.

Not every market is worth analyzing. This filters to markets that are:
- Binary (YES/NO resolution)
- Not too close to closing (stale odds)
- Not already fully resolved
- Within a probability range where edges can exist
- Not low-signal (coin flips, simulations, non-English)
"""
import re
from datetime import UTC, datetime
from typing import Any


# Markets matching these patterns are pure noise — Claude has no informational edge.
# Derived from backtest analysis showing 0.25 Brier (coin flip) on these categories.
_LOW_SIGNAL_PATTERNS = [
    re.compile(r"coin\s*flip", re.IGNORECASE),
    re.compile(r"daily\s*(coinflip|market|random)", re.IGNORECASE),
    re.compile(r"heads\s+or\s+tails|tails\s+or\s+heads", re.IGNORECASE),
    re.compile(r"simulation\s+match", re.IGNORECASE),
    re.compile(r"ALS\s+Tennis", re.IGNORECASE),
]

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


def is_tradeable(market: dict[str, Any], min_prob: float = 0.05, max_prob: float = 0.95) -> bool:
    """Return True if a market is worth analyzing.

    Skips markets that are fully priced in (near 0 or 1), already-closed,
    or low-signal (Claude has no edge on coin flips, simulations, etc.).
    """
    if market.get("isResolved"):
        return False
    if market.get("outcomeType") != "BINARY":
        return False

    question = market.get("question", "")
    if is_low_signal(question):
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
