"""Tests for price-band bucketing and binomial statistics."""
import pytest

from src.tracking.price_buckets import (
    BUCKET_EDGES,
    MIN_BUCKET_N,
    analyze_buckets,
    bucket_for_price,
    bucket_label,
    format_bucket_table,
    wilson_interval,
)

# ── Bucket assignment ───────────────────────────────────────────────────


def test_bucket_boundaries_are_half_open_lower_inclusive():
    """[lo, hi) — a price exactly on an edge belongs to the HIGHER bucket."""
    assert bucket_for_price(0.049) == 0
    assert bucket_for_price(0.05) == 1   # edge goes up, not down
    assert bucket_for_price(0.099) == 1
    assert bucket_for_price(0.10) == 2


def test_price_one_lands_in_top_bucket_not_dropped():
    """1.0 must have a home — the top bucket is [0.95, 1.0] inclusive."""
    assert bucket_for_price(1.0) == len(BUCKET_EDGES) - 1


def test_price_zero_lands_in_bottom_bucket():
    assert bucket_for_price(0.0) == 0


def test_out_of_range_prices_rejected():
    assert bucket_for_price(-0.01) is None
    assert bucket_for_price(1.01) is None


def test_bucket_labels_match_preregistration():
    """Labels are quoted in the write-up, so they are part of the contract."""
    labels = [bucket_label(i) for i in range(len(BUCKET_EDGES))]
    assert labels == [
        "0-5%", "5-10%", "10-20%", "20-35%", "35-50%",
        "50-65%", "65-80%", "80-90%", "90-95%", "95-100%",
    ]


# ── Wilson interval ─────────────────────────────────────────────────────


def test_wilson_interval_brackets_the_point_estimate():
    low, high = wilson_interval(successes=50, n=100)
    assert low < 0.5 < high


def test_wilson_stays_in_unit_interval_at_zero_successes():
    """The reason Wilson was chosen over the normal approximation: at p̂=0 the
    normal interval goes negative, which is nonsense for a probability."""
    low, high = wilson_interval(successes=0, n=200)
    assert low == pytest.approx(0.0)
    assert 0.0 < high < 0.05


def test_wilson_stays_in_unit_interval_at_all_successes():
    low, high = wilson_interval(successes=200, n=200)
    assert high == pytest.approx(1.0)
    assert 0.95 < low < 1.0


def test_wilson_narrows_as_sample_grows():
    narrow = wilson_interval(50, 100)
    narrower = wilson_interval(5000, 10000)
    assert (narrower[1] - narrower[0]) < (narrow[1] - narrow[0])


def test_wilson_degenerate_n_returns_full_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


# ── Bucket analysis ─────────────────────────────────────────────────────


def test_analyze_computes_realized_rate_and_difference():
    # 10 markets priced 0.02, of which 1 resolved YES → realized 0.10 vs 0.02
    obs = [(0.02, 1)] + [(0.02, 0)] * 9
    (result,) = analyze_buckets(obs)
    assert result.n == 10
    assert result.mean_price == pytest.approx(0.02)
    assert result.realized_rate == pytest.approx(0.10)
    assert result.diff == pytest.approx(0.08)


def test_analyze_flags_longshot_overpricing_as_negative_diff():
    """H1's signature: low band resolves YES LESS often than priced."""
    obs = [(0.08, 0)] * 98 + [(0.08, 1)] * 2  # priced 8%, resolves 2%
    (result,) = analyze_buckets(obs)
    assert result.diff < 0


def test_analyze_drops_invalid_rows():
    obs = [(0.5, 1), (1.5, 1), (0.5, 7), (-0.2, 0)]
    results = analyze_buckets(obs)
    assert sum(r.n for r in results) == 1  # only the first row survives


def test_analyze_separates_buckets():
    obs = [(0.02, 0)] * 5 + [(0.55, 1)] * 3
    results = analyze_buckets(obs)
    assert [r.label for r in results] == ["0-5%", "50-65%"]
    assert [r.n for r in results] == [5, 3]


def test_underpowered_flag_respects_threshold():
    small = analyze_buckets([(0.5, 1)] * (MIN_BUCKET_N - 1))[0]
    big = analyze_buckets([(0.5, 1)] * MIN_BUCKET_N)[0]
    assert small.underpowered
    assert not big.underpowered


def test_significance_requires_ci_excluding_zero():
    """A well-separated case is significant; a perfectly-calibrated one is not."""
    biased = analyze_buckets([(0.50, 0)] * 400)[0]      # priced 50%, never YES
    calibrated = analyze_buckets([(0.50, 1)] * 200 + [(0.50, 0)] * 200)[0]
    assert biased.significant
    assert not calibrated.significant


# ── Table rendering ─────────────────────────────────────────────────────


def test_table_warns_when_no_bucket_is_powered():
    out = format_bucket_table(analyze_buckets([(0.02, 0)] * 10), "t")
    assert "underpowered" in out.lower()
    assert f"NO bucket reaches N={MIN_BUCKET_N}" in out


def test_table_reports_powered_bucket_count():
    obs = [(0.02, 0)] * MIN_BUCKET_N + [(0.55, 1)] * 5
    out = format_bucket_table(analyze_buckets(obs), "t")
    assert "1 of 2 buckets are adequately powered" in out
