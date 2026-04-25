"""Central configuration — reads from environment variables with sensible defaults.

All tunable parameters live here. Never hardcode limits or API settings elsewhere.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _parse_bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes")


# Categories where Claude has proven edge (backtest-derived, 2026-04-01)
CATEGORY_WHITELIST: list[str] = [
    "competitive-gaming",
    "manifold-users",
    "personal-goals",
    "commitment-devices",
    "fun",
    "fairlyrandom",
]

# Categories where Claude has no edge or negative expected value
CATEGORY_BLACKLIST: list[str] = [
    "metamarkets",
    "tetraspace",
    "personal",
    "unranked",
    "will-i",
    "dailycoinflip",
    "nonpredictive-profits",
    "nonpredictive",
    # Legacy no-edge categories (real-time data dependent)
    "cricket",
    "ipl-2026",
    "sports-betting",
    "sports-default",
    "football",
    "crypto-speculation",
    "elections",
]


# Tags that trigger the web_search tool — markets where Claude's training-data
# cutoff makes it unreliable without live information. Previously these were
# blacklisted; now they flow through but with search enabled.
REALTIME_TAGS: list[str] = [
    "iran",
    "middle-east",
    "israeliran-conflict",
    "breaking-news",
    "current-events",
]


@dataclass(frozen=True)
class Settings:
    # API keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    manifold_api_key: str = os.getenv("MANIFOLD_API_KEY", "")

    # Budget (0 = calibration mode — log predictions but never execute trades)
    budget_daily_limit: float = float(os.getenv("BUDGET_DAILY_LIMIT", "0"))
    budget_total_limit: float = float(os.getenv("BUDGET_TOTAL_LIMIT", "50"))

    # Kelly Criterion
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.25"))
    min_edge_threshold: float = float(os.getenv("MIN_EDGE_THRESHOLD", "0.05"))
    max_position_pct: float = 0.05  # Never bet more than 5% of bankroll

    # Claude model
    model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Pipeline
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "1800"))
    max_markets_per_cycle: int = int(os.getenv("MAX_MARKETS_PER_CYCLE", "20"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Telegram notifications
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Paths
    db_path: str = os.getenv("DB_PATH", "predictions.db")

    # Polymarket (paper trading only)
    polymarket_enabled: bool = _parse_bool(
        os.getenv("POLYMARKET_ENABLED", "false")
    )
    polymarket_mode: str = os.getenv("POLYMARKET_MODE", "paper")
    polymarket_min_volume: int = int(os.getenv("POLYMARKET_MIN_VOLUME", "1000"))
    polymarket_api_base: str = os.getenv(
        "POLYMARKET_API_BASE", "https://gamma-api.polymarket.com"
    )
    polymarket_clob_base: str = os.getenv(
        "POLYMARKET_CLOB_BASE", "https://clob.polymarket.com"
    )

    # Category filtering
    category_filter_enabled: bool = _parse_bool(
        os.getenv("CATEGORY_FILTER_ENABLED", "true")
    )

    # Daily API cost budget (USD) — halts Claude calls when exceeded
    daily_api_budget: float = float(os.getenv("DAILY_API_BUDGET", "3.0"))

    # Prompt version for A/B testing
    active_prompt_version: str = os.getenv("ACTIVE_PROMPT_VERSION", "v2_market_aware")

    # Nightly calibration report via Telegram
    nightly_report_enabled: bool = _parse_bool(
        os.getenv("NIGHTLY_REPORT_ENABLED", "true")
    )

    # Ensemble estimator — run multiple Claude calls at different temperatures
    # and average the probabilities. Costs N× per call, so default is off.
    ensemble_enabled: bool = _parse_bool(
        os.getenv("ENSEMBLE_ENABLED", "false")
    )
    ensemble_samples: int = int(os.getenv("ENSEMBLE_SAMPLES", "3"))


settings = Settings()
