"""Central configuration — reads from environment variables with sensible defaults.

All tunable parameters live here. Never hardcode limits or API settings elsewhere.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


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
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    max_markets_per_cycle: int = int(os.getenv("MAX_MARKETS_PER_CYCLE", "20"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Paths
    db_path: str = os.getenv("DB_PATH", "predictions.db")


settings = Settings()
