"""Telegram notification bot — send-only alerts to a single chat.

Sends pipeline status updates so you don't have to SSH into the Pi.
Silently no-ops if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are not set.
"""
import logging

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def _is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


async def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat.

    Returns True if sent successfully, False otherwise.
    Silently returns False if Telegram is not configured.
    """
    if not _is_configured():
        return False

    url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


async def notify_cycle_summary(
    markets_scanned: int,
    estimates_made: int,
    trades_made: int,
    high_edge_markets: list[dict] | None = None,
) -> None:
    """Send a summary after each pipeline cycle."""
    lines = [
        "<b>Pipeline cycle complete</b>",
        f"Markets scanned: {markets_scanned}",
        f"Estimates made: {estimates_made}",
        f"Trades logged: {trades_made}",
    ]

    if high_edge_markets:
        lines.append("")
        lines.append("<b>High-edge opportunities (&gt;15%):</b>")
        for m in high_edge_markets:
            lines.append(
                f"  {m['question'][:50]}\n"
                f"  Edge: {m['edge']:+.1%} | Est: {m['estimate']:.0%} | Mkt: {m['market_price']:.0%}"
            )

    await send_message("\n".join(lines))


async def notify_error(error: str, context: str = "") -> None:
    """Send an error alert."""
    text = f"<b>Pipeline error</b>\n{error}"
    if context:
        text += f"\n<i>{context}</i>"
    await send_message(text)


async def notify_daily_summary(
    predictions_today: int,
    api_calls: int,
    est_cost_usd: float,
    mean_brier: float | None = None,
    platform_counts: dict[str, int] | None = None,
) -> None:
    """Send a daily summary — call this from a separate daily script or cron."""
    lines = [
        "<b>Daily summary</b>",
        f"Predictions: {predictions_today}",
    ]
    if platform_counts and len(platform_counts) > 1:
        for plat, count in sorted(platform_counts.items()):
            lines.append(f"  {plat}: {count}")
    lines.append(f"API calls: {api_calls}")
    lines.append(f"Est. cost: ${est_cost_usd:.3f}")
    if mean_brier is not None:
        lines.append(f"Mean Brier (7d): {mean_brier:.4f}")
    await send_message("\n".join(lines))
