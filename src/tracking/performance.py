"""P&L tracking, win rate, and ROI metrics.

Reads resolved trades from the DB and computes aggregate performance stats.
"""
from dataclasses import dataclass


@dataclass
class PerformanceStats:
    total_bets: int
    wins: int
    losses: int
    total_pnl: float
    roi: float  # (total_pnl / total_wagered) if total_wagered > 0 else 0


def compute_stats(trades: list[dict]) -> PerformanceStats:
    """Compute performance stats from a list of resolved trade dicts.

    Each trade dict must have: outcome ('win'|'loss'), size, pnl fields.
    """
    resolved = [t for t in trades if t.get("outcome") in ("win", "loss")]
    wins = sum(1 for t in resolved if t["outcome"] == "win")
    losses = sum(1 for t in resolved if t["outcome"] == "loss")
    total_pnl = sum(t.get("pnl", 0.0) for t in resolved)
    total_wagered = sum(t.get("size", 0.0) for t in resolved)
    roi = (total_pnl / total_wagered) if total_wagered > 0 else 0.0

    return PerformanceStats(
        total_bets=len(resolved),
        wins=wins,
        losses=losses,
        total_pnl=total_pnl,
        roi=roi,
    )
