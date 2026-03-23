"""Market scanner — filters Manifold markets by criteria.

Not every market is worth analyzing. This filters to markets that are:
- Binary (YES/NO resolution)
- Not too close to closing (stale odds)
- Not already fully resolved
- Within a probability range where edges can exist
"""
from datetime import UTC, datetime
from typing import Any


def is_tradeable(market: dict[str, Any], min_prob: float = 0.05, max_prob: float = 0.95) -> bool:
    """Return True if a market is worth analyzing.

    Skips markets that are fully priced in (near 0 or 1) and already-closed markets.
    """
    if market.get("isResolved"):
        return False
    if market.get("outcomeType") != "BINARY":
        return False  # Only handle binary YES/NO markets for now

    prob = market.get("probability", 0.5)
    if not (min_prob <= prob <= max_prob):
        return False  # Market is already nearly certain — no edge possible

    close_time_ms = market.get("closeTime")
    if close_time_ms:
        close_dt = datetime.fromtimestamp(close_time_ms / 1000, tz=UTC)
        now = datetime.now(tz=UTC)
        hours_remaining = (close_dt - now).total_seconds() / 3600
        if hours_remaining < 24:
            return False  # Closing too soon — odds may be stale or illiquid

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
