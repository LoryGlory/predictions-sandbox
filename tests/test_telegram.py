"""Unit tests for Telegram notification module."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.notifications.telegram import (
    notify_cycle_summary,
    notify_daily_summary,
    notify_error,
    send_message,
)


@dataclass(frozen=True)
class FakeSettings:
    telegram_bot_token: str = "fake-token"
    telegram_chat_id: str = "12345"


@pytest.fixture(autouse=True)
def _fake_settings(monkeypatch):
    """Provide fake Telegram settings for all tests."""
    monkeypatch.setattr("src.notifications.telegram.settings", FakeSettings())


async def test_send_message_posts_to_telegram_api():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None

    with patch("src.notifications.telegram.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await send_message("hello")

    assert result is True
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "fake-token" in call_kwargs.args[0]
    assert call_kwargs.kwargs["json"]["text"] == "hello"
    assert call_kwargs.kwargs["json"]["chat_id"] == "12345"


async def test_send_message_returns_false_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        "src.notifications.telegram.settings",
        FakeSettings(telegram_bot_token=""),
    )
    result = await send_message("hello")
    assert result is False


async def test_send_message_returns_false_on_http_error():
    with patch("src.notifications.telegram.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await send_message("hello")

    assert result is False


async def test_notify_cycle_summary_includes_stats():
    with patch("src.notifications.telegram.send_message", new_callable=AsyncMock) as mock_send:
        await notify_cycle_summary(
            markets_scanned=15,
            estimates_made=8,
            trades_made=2,
        )

    mock_send.assert_called_once()
    text = mock_send.call_args.args[0]
    assert "15" in text
    assert "8" in text
    assert "2" in text


async def test_notify_cycle_summary_includes_high_edge():
    with patch("src.notifications.telegram.send_message", new_callable=AsyncMock) as mock_send:
        await notify_cycle_summary(
            markets_scanned=10,
            estimates_made=5,
            trades_made=1,
            high_edge_markets=[{
                "question": "Will X happen?",
                "edge": 0.20,
                "estimate": 0.75,
                "market_price": 0.55,
            }],
        )

    text = mock_send.call_args.args[0]
    assert "Will X happen?" in text
    assert "Edge" in text


async def test_notify_error_sends_message():
    with patch("src.notifications.telegram.send_message", new_callable=AsyncMock) as mock_send:
        await notify_error("something broke", context="run_cycle")

    text = mock_send.call_args.args[0]
    assert "something broke" in text
    assert "run_cycle" in text


async def test_notify_daily_summary_includes_cost():
    with patch("src.notifications.telegram.send_message", new_callable=AsyncMock) as mock_send:
        await notify_daily_summary(
            predictions_today=42,
            api_calls=42,
            est_cost_usd=0.126,
            mean_brier=0.22,
        )

    text = mock_send.call_args.args[0]
    assert "42" in text
    assert "$0.126" in text
    assert "0.2200" in text
