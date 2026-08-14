"""Tests for historical price reconstruction.

The central risk this guards against: reading a resolved market's CURRENT
probability (which equals its outcome) instead of its pre-close traded price.
"""
import pytest

from src.markets.price_history import (
    MS_PER_HOUR,
    PriceHistoryCache,
    PriceSnapshot,
    cutoff_for_close,
    fetch_price_at_cutoff,
    last_price_before,
)

CLOSE = 1_800_000_000_000  # arbitrary epoch-ms close time


def _bet(bet_id: str, hours_before_close: float, prob_after: float) -> dict:
    return {
        "id": bet_id,
        "createdTime": int(CLOSE - hours_before_close * MS_PER_HOUR),
        "probAfter": prob_after,
    }


# ── Cutoff arithmetic ───────────────────────────────────────────────────


def test_cutoff_is_exactly_n_hours_before_close():
    assert cutoff_for_close(CLOSE, 24) == CLOSE - 24 * MS_PER_HOUR


# ── last_price_before ───────────────────────────────────────────────────


def test_picks_latest_trade_strictly_before_cutoff():
    cutoff = cutoff_for_close(CLOSE, 24)
    bets = [
        _bet("late", 2, 0.90),    # after cutoff — must be ignored
        _bet("target", 25, 0.30),  # latest before cutoff — this one
        _bet("early", 90, 0.10),
    ]
    price, bet_id, _ = last_price_before(bets, cutoff)
    assert price == pytest.approx(0.30)
    assert bet_id == "target"


def test_ignores_trades_after_the_cutoff_entirely():
    """The whole point: post-cutoff prices leak information about the outcome."""
    cutoff = cutoff_for_close(CLOSE, 24)
    bets = [_bet("resolution_drift", 0.5, 0.99)]
    assert last_price_before(bets, cutoff) == (None, None, None)


def test_trade_exactly_at_cutoff_is_excluded():
    """'Strictly before' — a boundary trade is not counted."""
    cutoff = cutoff_for_close(CLOSE, 24)
    at_boundary = {"id": "b", "createdTime": cutoff, "probAfter": 0.4}
    assert last_price_before([at_boundary], cutoff) == (None, None, None)


def test_does_not_rely_on_api_ordering():
    """Manifold returns newest-first today; correctness must not depend on it."""
    cutoff = cutoff_for_close(CLOSE, 24)
    ascending = [_bet("old", 100, 0.1), _bet("mid", 60, 0.2), _bet("new", 30, 0.35)]
    price, bet_id, _ = last_price_before(ascending, cutoff)
    assert bet_id == "new"
    assert price == pytest.approx(0.35)


def test_skips_malformed_bets():
    cutoff = cutoff_for_close(CLOSE, 24)
    bets = [
        {"id": "no_time", "probAfter": 0.5},
        {"id": "no_prob", "createdTime": CLOSE - 50 * MS_PER_HOUR},
        _bet("good", 40, 0.22),
    ]
    price, bet_id, _ = last_price_before(bets, cutoff)
    assert bet_id == "good"
    assert price == pytest.approx(0.22)


def test_empty_history_yields_nothing():
    assert last_price_before([], cutoff_for_close(CLOSE, 24)) == (None, None, None)


# ── Cache ───────────────────────────────────────────────────────────────


def test_cache_roundtrip(tmp_path):
    cache = PriceHistoryCache(tmp_path, 24)
    snap = PriceSnapshot("mkt1", 123, 0.42, "bet1", 100, 1)
    cache.put(snap)
    assert cache.get("mkt1") == snap


def test_cache_miss_returns_none(tmp_path):
    assert PriceHistoryCache(tmp_path, 24).get("nope") is None


def test_cache_is_keyed_by_cutoff_hours(tmp_path):
    """Changing the pre-registered cutoff must invalidate, not serve stale."""
    PriceHistoryCache(tmp_path, 24).put(PriceSnapshot("m", 1, 0.3, "b", 0, 1))
    assert PriceHistoryCache(tmp_path, 48).get("m") is None


def test_cache_survives_corrupt_entry(tmp_path):
    cache = PriceHistoryCache(tmp_path, 24)
    cache.put(PriceSnapshot("m", 1, 0.3, "b", 0, 1))
    (cache.dir / "m.json").write_text("{ not json")
    assert cache.get("m") is None  # degrades to a refetch, does not crash


# ── fetch_price_at_cutoff ───────────────────────────────────────────────


class _FakeClient:
    """Serves canned pages, newest-first, and counts requests."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages
        self.calls = 0

    async def get_market_bets(self, contract_id, limit=1000, before=None):
        page = self.pages[self.calls] if self.calls < len(self.pages) else []
        self.calls += 1
        return page


@pytest.mark.asyncio
async def test_fetch_stops_on_first_page_when_cutoff_found(tmp_path):
    cutoff = cutoff_for_close(CLOSE, 24)
    client = _FakeClient([[_bet("a", 2, 0.9), _bet("b", 30, 0.25)]])
    snap = await fetch_price_at_cutoff(client, "m", cutoff, PriceHistoryCache(tmp_path, 24))
    assert snap.price == pytest.approx(0.25)
    assert client.calls == 1  # common case costs one request


@pytest.mark.asyncio
async def test_fetch_pages_back_when_all_trades_are_recent(tmp_path):
    cutoff = cutoff_for_close(CLOSE, 24)
    client = _FakeClient([
        [_bet("r1", 5, 0.8), _bet("r2", 6, 0.8)],   # all after cutoff
        [_bet("old", 40, 0.15)],                     # found here
    ])
    snap = await fetch_price_at_cutoff(client, "m", cutoff, PriceHistoryCache(tmp_path, 24))
    assert snap.price == pytest.approx(0.15)
    assert client.calls == 2
    assert snap.pages_fetched == 2


@pytest.mark.asyncio
async def test_fetch_marks_unusable_when_no_pre_cutoff_trade(tmp_path):
    """A market whose first trade came inside the final 24h has no usable
    price and must be EXCLUDED from the sample, not defaulted to something."""
    cutoff = cutoff_for_close(CLOSE, 24)
    client = _FakeClient([[_bet("only", 3, 0.7)], []])
    snap = await fetch_price_at_cutoff(client, "m", cutoff, PriceHistoryCache(tmp_path, 24))
    assert snap.price is None
    assert not snap.usable


@pytest.mark.asyncio
async def test_cached_result_skips_the_network(tmp_path):
    cutoff = cutoff_for_close(CLOSE, 24)
    cache = PriceHistoryCache(tmp_path, 24)
    cache.put(PriceSnapshot("m", cutoff, 0.33, "b", 1, 1))
    client = _FakeClient([[_bet("x", 30, 0.99)]])
    snap = await fetch_price_at_cutoff(client, "m", cutoff, cache)
    assert snap.price == pytest.approx(0.33)
    assert client.calls == 0


@pytest.mark.asyncio
async def test_cache_entry_for_different_cutoff_is_ignored(tmp_path):
    cache = PriceHistoryCache(tmp_path, 24)
    cache.put(PriceSnapshot("m", 999, 0.33, "b", 1, 1))  # stale cutoff
    client = _FakeClient([[_bet("x", 30, 0.44)]])
    snap = await fetch_price_at_cutoff(client, "m", cutoff_for_close(CLOSE, 24), cache)
    assert snap.price == pytest.approx(0.44)
    assert client.calls == 1
