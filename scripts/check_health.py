#!/usr/bin/env python3
"""Health check — verifies DB is accessible and API keys are present.

Exit code 0 = healthy, 1 = unhealthy. Suitable for monitoring/alerting.
"""
import asyncio
import sys

from config.settings import settings
from src.db.connection import get_db


async def check() -> bool:
    issues: list[str] = []

    if not settings.anthropic_api_key:
        issues.append("ANTHROPIC_API_KEY not set")
    if not settings.manifold_api_key:
        issues.append("MANIFOLD_API_KEY not set (read-only mode active)")

    try:
        async with get_db() as db:
            await db.execute("SELECT 1")
    except Exception as e:
        issues.append(f"DB inaccessible: {e}")

    if issues:
        for issue in issues:
            print(f"[WARN] {issue}", file=sys.stderr)
        return False

    print("[OK] All systems healthy")
    return True


if __name__ == "__main__":
    ok = asyncio.run(check())
    sys.exit(0 if ok else 1)
