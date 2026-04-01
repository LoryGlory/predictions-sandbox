"""Backtesting against resolved Manifold markets.

Fetches resolved binary markets from Manifold, asks Claude to estimate the
probability WITHOUT revealing the outcome, then scores predictions using
Brier score and compares against the market-price baseline.

This lets us evaluate Claude's calibration on historical data immediately,
without waiting for live markets to resolve.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.analysis.estimator import Estimator, ProbabilityEstimate
from src.db.connection import get_db
from src.markets.manifold import ManifoldClient
from src.tracking.calibration import brier_score, brier_skill_score, mean_brier_score

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Result for a single market in the backtest."""

    market_id: str
    question: str
    resolution: str          # 'YES' or 'NO'
    outcome: int             # 1 = YES, 0 = NO
    market_price: float      # price just before resolution (baseline)
    estimated_probability: float
    confidence: str
    reasoning: str
    model_brier: float
    baseline_brier: float


@dataclass
class BacktestReport:
    """Aggregate calibration report for a backtest run."""

    results: list[BacktestResult] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.results)

    @property
    def mean_model_brier(self) -> float:
        if not self.results:
            return 0.0
        return mean_brier_score(
            [r.estimated_probability for r in self.results],
            [r.outcome for r in self.results],
        )

    @property
    def mean_baseline_brier(self) -> float:
        if not self.results:
            return 0.0
        return mean_brier_score(
            [r.market_price for r in self.results],
            [r.outcome for r in self.results],
        )

    @property
    def skill_score(self) -> float:
        baseline = self.mean_baseline_brier
        if baseline == 0:
            return 0.0
        return brier_skill_score(self.mean_model_brier, baseline)

    def print_report(self) -> None:
        """Print a human-readable calibration report to stdout."""
        print(f"\n{'=' * 60}")
        print(f"BACKTEST REPORT — {self.count} resolved markets")
        print(f"{'=' * 60}")
        print(f"Model mean Brier score:    {self.mean_model_brier:.4f}  (lower = better)")
        print(f"Baseline mean Brier score: {self.mean_baseline_brier:.4f}  (market price as prediction)")
        print(f"Brier Skill Score:         {self.skill_score:+.4f}  (positive = beats market)")
        print(f"{'=' * 60}")

        if self.count > 0:
            print("\nPer-market breakdown:")
            print(f"{'Question':<50} {'Outcome':<8} {'Mkt%':<7} {'Est%':<7} {'ModelB':<8} {'BaseB':<7}")
            print("-" * 90)
            for r in self.results:
                print(
                    f"{r.question[:49]:<50} "
                    f"{'YES' if r.outcome else 'NO':<8} "
                    f"{r.market_price:.1%}  "
                    f"{r.estimated_probability:.1%}  "
                    f"{r.model_brier:.4f}   "
                    f"{r.baseline_brier:.4f}"
                )


async def fetch_resolved_markets(
    client: ManifoldClient,
    count: int = 20,
) -> list[dict]:
    """Fetch resolved binary markets from Manifold.

    Args:
        client: Open ManifoldClient context.
        count: Target number of resolved binary markets to return.

    Returns:
        List of market dicts that are resolved as YES or NO.
    """
    resolved = []
    before: str | None = None
    max_pages = 10  # safety limit to avoid infinite loops

    for _ in range(max_pages):
        batch = await client.get_markets(limit=100, before=before)
        if not batch:
            break

        for market in batch:
            if (
                market.get("isResolved")
                and market.get("outcomeType") == "BINARY"
                and market.get("resolution") in ("YES", "NO")
                and market.get("probability") is not None
            ):
                resolved.append(market)
                if len(resolved) >= count:
                    return resolved

        before = batch[-1]["id"]

    logger.warning("Only found %d resolved markets (requested %d)", len(resolved), count)
    return resolved


async def run_backtest(
    count: int = 20,
    estimator: Estimator | None = None,
) -> BacktestReport:
    """Run a backtest against resolved Manifold markets.

    Args:
        count: Number of resolved markets to test against.
        estimator: Estimator instance. Created from settings if not provided.

    Returns:
        BacktestReport with per-market results and aggregate metrics.
    """
    if estimator is None:
        estimator = Estimator()

    report = BacktestReport()

    async with ManifoldClient() as client:
        markets = await fetch_resolved_markets(client, count=count)

    logger.info("Running backtest on %d resolved markets", len(markets))

    async with get_db() as db:
        for market in markets:
            question = market.get("question", "")
            resolution = market.get("resolution", "")
            outcome = 1 if resolution == "YES" else 0
            external_id = market.get("id", "")
            # Use the probability at resolution time as the market baseline
            market_price = market.get("probability", 0.5)

            # Fetch full market details for tags (list endpoint doesn't include them)
            raw_tags = market.get("groupSlugs") or market.get("tags") or []
            if not raw_tags and external_id:
                try:
                    async with ManifoldClient() as detail_client:
                        full_market = await detail_client.get_market(external_id)
                    raw_tags = full_market.get("groupSlugs") or full_market.get("tags") or []
                except Exception as e:
                    logger.debug("Could not fetch tags for %s: %s", external_id, e)
            tags_json = json.dumps(raw_tags) if raw_tags else None

            logger.info("Estimating: %s", question[:60])
            try:
                # NOTE: We pass only the question — NOT the resolution —
                # so Claude can't cheat by reading the outcome.
                estimate: ProbabilityEstimate = await estimator.estimate(
                    question=question,
                    market_price=None,  # hide market price to get a fully independent estimate
                )
            except Exception as e:
                logger.error("Estimation failed for '%s': %s", question[:50], e)
                continue

            model_brier = brier_score(estimate.estimated_probability, outcome)
            baseline_brier = brier_score(market_price, outcome)

            report.results.append(
                BacktestResult(
                    market_id=external_id,
                    question=question,
                    resolution=resolution,
                    outcome=outcome,
                    market_price=market_price,
                    estimated_probability=estimate.estimated_probability,
                    confidence=estimate.confidence,
                    reasoning=estimate.reasoning,
                    model_brier=model_brier,
                    baseline_brier=baseline_brier,
                )
            )
            logger.info(
                "  outcome=%s market=%.1f%% estimate=%.1f%% model_brier=%.4f baseline_brier=%.4f",
                resolution,
                market_price * 100,
                estimate.estimated_probability * 100,
                model_brier,
                baseline_brier,
            )

            # Persist to DB for category analysis
            await db.execute(
                """INSERT INTO markets (platform, external_id, question, current_price, tags)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET
                       current_price=excluded.current_price,
                       tags=excluded.tags""",
                ("manifold", external_id, question, market_price, tags_json),
            )
            await db.commit()

            async with db.execute(
                "SELECT id FROM markets WHERE platform=? AND external_id=?",
                ("manifold", external_id),
            ) as cur:
                row = await cur.fetchone()
            market_db_id = row["id"]

            await db.execute(
                """INSERT INTO predictions (market_id, model, estimated_prob, confidence, reasoning)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    market_db_id,
                    estimate.model,
                    estimate.estimated_probability,
                    estimate.confidence,
                    estimate.reasoning,
                ),
            )
            await db.commit()

            async with db.execute("SELECT last_insert_rowid() as id") as cur:
                pred_row = await cur.fetchone()
            prediction_db_id = pred_row["id"]

            await db.execute(
                """INSERT INTO calibration
                   (prediction_id, predicted_prob, actual_outcome, brier_score, resolved_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (prediction_db_id, estimate.estimated_probability, outcome, model_brier),
            )
            await db.commit()

    return report
