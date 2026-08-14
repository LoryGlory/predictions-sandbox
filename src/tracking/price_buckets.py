"""Price-band bucketing and binomial statistics for favorite-longshot analysis.

Pure functions, no I/O — same character as calibration.py.

The bucket edges and the minimum-N threshold in this module are FROZEN by the
pre-registration in experiment-longshot-bias.md. Changing them after data has
been seen invalidates the run. They live in code (not a config file) so that a
change shows up in a diff and has to be justified in a commit message.

The favorite-longshot bias appears as realized YES frequency falling BELOW the
traded price in low buckets, while sitting at or above price in high buckets.
"""
import math
from dataclasses import dataclass

# ── FROZEN 2026-08-14 by experiment-longshot-bias.md ────────────────────
# Upper edges of each bucket. Assignment is [lower, upper) except the final
# bucket, which is [0.95, 1.0] so that a price of exactly 1.0 has a home.
BUCKET_EDGES: tuple[float, ...] = (
    0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 1.00,
)

# Any bucket below this N is reported as "underpowered" and never as a finding.
MIN_BUCKET_N: int = 300
# ────────────────────────────────────────────────────────────────────────

Z_95 = 1.959963984540054  # two-sided 95% normal quantile


@dataclass(frozen=True)
class BucketResult:
    """Per-bucket outcome statistics.

    diff_* fields are (realized rate − mean price): negative means the band
    resolves YES less often than it was priced, i.e. longshot overpricing.
    """

    label: str
    n: int
    mean_price: float
    realized_rate: float
    diff: float
    diff_ci_low: float
    diff_ci_high: float

    @property
    def underpowered(self) -> bool:
        return self.n < MIN_BUCKET_N

    @property
    def significant(self) -> bool:
        """True if the 95% interval on the difference excludes zero.

        Note this says nothing about whether the bucket is well-powered —
        always read alongside `underpowered`.
        """
        return self.diff_ci_low > 0 or self.diff_ci_high < 0


def bucket_label(index: int) -> str:
    """Human-readable label for a bucket index, e.g. '5-10%'."""
    lower = 0.0 if index == 0 else BUCKET_EDGES[index - 1]
    upper = BUCKET_EDGES[index]
    return f"{lower * 100:g}-{upper * 100:g}%"


def bucket_for_price(price: float) -> int | None:
    """Return the bucket index for a price, or None if outside [0, 1].

    Prices at exactly 0.0 or 1.0 are kept (they are real traded states on a
    CPMM only in the limit, but appear in data and excluding them silently
    would bias the extreme buckets).
    """
    if not 0.0 <= price <= 1.0:
        return None
    for i, upper in enumerate(BUCKET_EDGES):
        if price < upper:
            return i
    return len(BUCKET_EDGES) - 1  # price == 1.0 lands in the top bucket


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because the buckets that matter most
    here (0-5%, 5-10%) have proportions near zero, where the normal
    approximation produces intervals that are too narrow and can dip below 0.
    """
    if n <= 0:
        return (0.0, 1.0)
    p_hat = successes / n
    denom = 1.0 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def analyze_buckets(
    observations: list[tuple[float, int]],
) -> list[BucketResult]:
    """Bucket (price, outcome) pairs and compute per-bucket statistics.

    Args:
        observations: (price, outcome) where price is the traded probability
            at the pre-registered snapshot and outcome is 1 for YES, 0 for NO.
            Pairs with out-of-range prices or non-binary outcomes are dropped.

    Returns:
        One BucketResult per non-empty bucket, in ascending price order.

    The interval on the difference is the Wilson interval on the realized rate,
    shifted by the bucket's mean price. This treats mean price as fixed — its
    own sampling variance is negligible next to the binomial noise on the rate
    at these sample sizes, but the simplification is stated here rather than
    buried.
    """
    grouped: dict[int, list[tuple[float, int]]] = {}
    for price, outcome in observations:
        if outcome not in (0, 1):
            continue
        idx = bucket_for_price(price)
        if idx is None:
            continue
        grouped.setdefault(idx, []).append((price, outcome))

    results: list[BucketResult] = []
    for idx in sorted(grouped):
        rows = grouped[idx]
        n = len(rows)
        mean_price = sum(p for p, _ in rows) / n
        successes = sum(o for _, o in rows)
        realized = successes / n
        ci_low, ci_high = wilson_interval(successes, n)
        results.append(
            BucketResult(
                label=bucket_label(idx),
                n=n,
                mean_price=mean_price,
                realized_rate=realized,
                diff=realized - mean_price,
                diff_ci_low=ci_low - mean_price,
                diff_ci_high=ci_high - mean_price,
            )
        )
    return results


def format_bucket_table(results: list[BucketResult], title: str) -> str:
    """Render bucket results as a fixed-width table with power/significance flags."""
    lines = [
        title,
        "-" * 78,
        f"{'bucket':<10} {'N':>6} {'price':>8} {'realized':>9} "
        f"{'diff':>8} {'95% CI on diff':>20}  flag",
        "-" * 78,
    ]
    for r in results:
        flag = ""
        if r.underpowered:
            flag = f"underpowered (<{MIN_BUCKET_N})"
        elif r.significant:
            flag = "significant"
        ci = f"[{r.diff_ci_low:+.3f}, {r.diff_ci_high:+.3f}]"
        lines.append(
            f"{r.label:<10} {r.n:>6} {r.mean_price:>8.3f} {r.realized_rate:>9.3f} "
            f"{r.diff:>+8.3f} {ci:>20}  {flag}"
        )
    lines.append("-" * 78)

    powered = [r for r in results if not r.underpowered]
    if not powered:
        lines.append(
            f"NO bucket reaches N={MIN_BUCKET_N}. Per pre-registration this is an "
            "underpowered run and\nsupports no finding in either direction."
        )
    else:
        lines.append(
            f"{len(powered)} of {len(results)} buckets are adequately powered "
            f"(N >= {MIN_BUCKET_N})."
        )
    return "\n".join(lines)
