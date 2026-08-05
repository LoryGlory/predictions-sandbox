"""Manifold Markets async API client.

Manifold is free to use, has a public REST API, and uses 'mana' (M$) as its
currency. No real money involved, making it ideal for calibration.

API docs: https://docs.manifold.markets/api
"""
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings

MANIFOLD_BASE = "https://api.manifold.markets/v0"
_ERR_CONTEXT_MANAGER = "ManifoldClient must be used as async context manager"


class ManifoldClient:
    """Async HTTP client for the Manifold Markets API.

    Usage:
        async with ManifoldClient() as client:
            markets = await client.get_markets(limit=20)
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.manifold_api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ManifoldClient":
        headers = {"Authorization": f"Key {self._api_key}"} if self._api_key else {}
        self._client = httpx.AsyncClient(
            base_url=MANIFOLD_BASE,
            headers=headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_markets(
        self,
        limit: int = 20,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch a list of active markets.

        Args:
            limit: Number of markets to return (max 1000 per Manifold API).
            before: Pagination cursor — ID of the last market from a previous page.

        Returns:
            List of market dicts with at minimum: id, question, probability, closeTime.
        """
        assert self._client is not None, _ERR_CONTEXT_MANAGER
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before

        resp = await self._client.get("/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_market(self, market_id: str) -> dict[str, Any]:
        """Fetch a single market by ID."""
        assert self._client is not None, _ERR_CONTEXT_MANAGER
        resp = await self._client.get(f"/market/{market_id}")
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_me(self) -> dict[str, Any]:
        """Return the authenticated user's profile, including current mana balance."""
        assert self._client is not None, _ERR_CONTEXT_MANAGER
        resp = await self._client.get("/me")
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_bets(self, user_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        """Fetch a user's bets (most recent first). Read-only — safe to retry."""
        assert self._client is not None, _ERR_CONTEXT_MANAGER
        resp = await self._client.get(
            "/bets", params={"userId": user_id, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    # NO @retry here — deliberately. /bet is a non-idempotent, money-moving
    # POST with no idempotency key. A timeout does not mean the bet failed:
    # Manifold may have executed it and only the response was lost. Retrying
    # would place the same bet again with real mana. One attempt, and the
    # caller treats any exception as "state unknown" (see executor).
    async def place_bet(
        self,
        market_id: str,
        outcome: str,
        amount: float,
    ) -> dict[str, Any]:
        """Place a bet on a market. Single attempt — never retried.

        Args:
            market_id: Manifold market ID.
            outcome: 'YES' or 'NO'.
            amount: Amount in mana (M$).

        Returns:
            Bet confirmation dict from Manifold.
        """
        assert self._client is not None, _ERR_CONTEXT_MANAGER
        resp = await self._client.post(
            "/bet",
            json={"contractId": market_id, "outcome": outcome, "amount": amount},
        )
        resp.raise_for_status()
        return resp.json()
