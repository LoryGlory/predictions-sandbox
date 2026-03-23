"""Unit tests for retry logic — verifies tenacity decorators are in place."""

import httpx
import pytest

from src.analysis.estimator import Estimator
from src.markets.manifold import ManifoldClient


async def test_manifold_get_markets_retries_on_http_error():
    """get_markets should retry on httpx.HTTPError up to 3 times."""
    async with ManifoldClient(api_key="test") as client:
        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("Connection refused")

        client._client.get = mock_get

        with pytest.raises(httpx.ConnectError):
            await client.get_markets(limit=5)

    assert call_count == 3  # 3 attempts total


async def test_estimator_has_retry_method():
    """Estimator._call_api exists and has retry decorator."""
    e = Estimator(api_key="dummy")
    assert hasattr(e, "_call_api")
    assert hasattr(e._call_api, "retry")  # tenacity adds .retry attribute
