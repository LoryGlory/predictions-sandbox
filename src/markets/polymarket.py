"""Polymarket API client — paper trading only.

Uses two public APIs (no authentication required):
- Gamma API: events, markets, tags, search
- CLOB API: price, midpoint, spread

NO real trading. NO wallet integration. NO CLOB authentication.
"""
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings

logger = logging.getLogger(__name__)

_ERR_CONTEXT_MANAGER = "PolymarketClient must be used as async context manager"


class PolymarketClient:
    """Async HTTP client for Polymarket's public APIs.

    Usage:
        async with PolymarketClient() as client:
            events = await client.get_events()
    """

    def __init__(self) -> None:
        self._gamma: httpx.AsyncClient | None = None
        self._clob: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PolymarketClient":
        self._gamma = httpx.AsyncClient(
            base_url=settings.polymarket_api_base,
            timeout=30.0,
        )
        self._clob = httpx.AsyncClient(
            base_url=settings.polymarket_clob_base,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._gamma:
            await self._gamma.aclose()
        if self._clob:
            await self._clob.aclose()

    # ── Gamma API (market discovery) ─────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_events(self, limit: int = 50, active: bool = True) -> list[dict[str, Any]]:
        """Fetch events with active markets."""
        assert self._gamma is not None, _ERR_CONTEXT_MANAGER
        params: dict[str, Any] = {"limit": limit, "active": active}
        resp = await self._gamma.get("/events", params=params)
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_markets(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch markets with current prices."""
        assert self._gamma is not None, _ERR_CONTEXT_MANAGER
        resp = await self._gamma.get("/markets", params={"limit": limit})
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_market(self, market_id: str) -> dict[str, Any]:
        """Fetch a single market by id (for resolution checks)."""
        assert self._gamma is not None, _ERR_CONTEXT_MANAGER
        resp = await self._gamma.get(f"/markets/{market_id}")
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_event_tags(self, event_id: str) -> list[str]:
        """Get tags for an event (for category filtering)."""
        assert self._gamma is not None, _ERR_CONTEXT_MANAGER
        resp = await self._gamma.get(f"/events/{event_id}/tags")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("tags", [])

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def search_markets(self, query: str) -> list[dict[str, Any]]:
        """Search markets by keyword."""
        assert self._gamma is not None, _ERR_CONTEXT_MANAGER
        resp = await self._gamma.get("/search", params={"q": query})
        resp.raise_for_status()
        return resp.json()

    # ── CLOB API (pricing data, public endpoints only) ───────────────────

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_price(self, token_id: str) -> dict[str, Any]:
        """Get current price for a market token."""
        assert self._clob is not None, _ERR_CONTEXT_MANAGER
        resp = await self._clob.get("/price", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_midpoint(self, token_id: str) -> dict[str, Any]:
        """Get midpoint price (better than raw bid/ask)."""
        assert self._clob is not None, _ERR_CONTEXT_MANAGER
        resp = await self._clob.get("/midpoint", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_spread(self, token_id: str) -> dict[str, Any]:
        """Get bid/ask spread (needed for simulated transaction costs)."""
        assert self._clob is not None, _ERR_CONTEXT_MANAGER
        resp = await self._clob.get("/spread", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()
