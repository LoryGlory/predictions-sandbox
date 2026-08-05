"""Tests for TradeExecutor — paper and live paths, plus safety caps.

No real API calls — the Manifold client is fully mocked.
"""
import asyncio
import time
from unittest.mock import patch

import pytest

from src.trading.executor import TradeExecutor


class _FakeGuardian:
    """Minimal stand-in for BudgetGuardian — never blocks."""

    def check_and_record(self, _amount: float) -> None:
        return None


class _FakeManifoldClient:
    """Replaces ManifoldClient for tests. Records calls and returns a stub bet."""

    def __init__(self, response: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response or {
            "id": "bet_abc123",
            "amount": 5,
            "probAfter": 0.62,
        }
        self.raise_exc = raise_exc

    async def place_bet(self, market_id: str, outcome: str, amount: int) -> dict:
        self.calls.append({"market_id": market_id, "outcome": outcome, "amount": amount})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _market(
    mid: str = "mkt_1",
    q: str = "Will X happen?",
    close_in_days: float = 30.0,
) -> dict:
    """Build a fake Manifold market dict. Default closeTime is 30d from now,
    well inside the LIVE_MAX_DAYS_TO_CLOSE=90 horizon used by tests."""
    close_time_ms = int((time.time() + close_in_days * 86400) * 1000)
    return {
        "id": mid,
        "question": q,
        "probability": 0.55,
        "closeTime": close_time_ms,
    }


def _mock_settings(**overrides):
    """Patch settings used by the executor."""
    from config.settings import Settings
    defaults = {
        "manifold_mode": "live",
        "live_max_bet_mana": 5,
        "live_max_bets_per_cycle": 3,
        "live_max_bets_per_day": 20,
        "live_max_days_to_close": 90,
    }
    defaults.update(overrides)
    # Other Settings fields use their dataclass defaults
    return Settings(**defaults)


# ── Paper mode tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_mode_returns_paper_trade():
    """When paper_mode=True the trade is logged as paper without API call."""
    executor = TradeExecutor(guardian=_FakeGuardian(), paper_mode=True)
    trade = await executor.execute(_market(), direction="yes", bet_size=10.0)
    assert trade is not None
    assert trade["is_paper"] == 1
    assert "live_bet_id" not in trade


@pytest.mark.asyncio
async def test_zero_bet_size_skipped():
    executor = TradeExecutor(guardian=_FakeGuardian(), paper_mode=True)
    trade = await executor.execute(_market(), direction="yes", bet_size=0.0)
    assert trade is None


@pytest.mark.asyncio
async def test_polymarket_always_paper_even_when_live_mode():
    """Polymarket has no wallet; it must stay paper regardless of MANIFOLD_MODE."""
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(guardian=_FakeGuardian(), paper_mode=False)
        trade = await executor.execute(
            _market(), direction="no", bet_size=2.5, platform="polymarket",
        )
        assert trade is not None
        assert trade["is_paper"] == 1
        assert "spread_cost" in trade
        assert "live_bet_id" not in trade


# ── Live mode tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_places_bet_and_caps_size():
    """In live mode, the bet hits the Manifold API and gets capped at MAX_BET_MANA."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(live_max_bet_mana=5)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=20.0)
    assert trade is not None
    assert trade["is_paper"] == 0
    assert trade["size"] == pytest.approx(5.0)  # capped from 20 to 5
    assert trade["live_bet_id"] == "bet_abc123"
    assert fake_client.calls == [
        {"market_id": "mkt_1", "outcome": "YES", "amount": 5},
    ]


@pytest.mark.asyncio
async def test_paper_mode_master_kill_switch_overrides_live_setting():
    """paper_mode=True wins even if MANIFOLD_MODE=live."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=True, manifold_client=fake_client,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=10.0)
    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_live_mode_off_when_setting_is_paper():
    """paper_mode=False but MANIFOLD_MODE=paper → still paper."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(manifold_mode="paper")):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=10.0)
    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []


# ── Safety cap tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cycle_cap_downgrades_to_paper():
    """After LIVE_MAX_BETS_PER_CYCLE successful live bets, further bets become paper."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(live_max_bets_per_cycle=2)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        first = await executor.execute(_market("m1"), direction="yes", bet_size=3.0)
        second = await executor.execute(_market("m2"), direction="yes", bet_size=3.0)
        third = await executor.execute(_market("m3"), direction="yes", bet_size=3.0)

    assert first["is_paper"] == 0
    assert second["is_paper"] == 0
    assert third["is_paper"] == 1  # blocked, downgraded
    assert len(fake_client.calls) == 2  # only first two hit the API


@pytest.mark.asyncio
async def test_daily_cap_downgrades_to_paper():
    """Daily-count provider returning >= cap blocks live bets."""
    fake_client = _FakeManifoldClient()

    async def provider() -> int:
        return 20  # at the cap

    with patch("src.trading.executor.settings", _mock_settings(live_max_bets_per_day=20)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(),
            paper_mode=False,
            manifold_client=fake_client,
            daily_live_count_provider=provider,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=3.0)

    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_reset_cycle_counter_clears_block():
    """Calling reset_cycle_counter() lets a new cycle start fresh."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(live_max_bets_per_cycle=1)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        await executor.execute(_market("m1"), direction="yes", bet_size=3.0)
        blocked = await executor.execute(_market("m2"), direction="yes", bet_size=3.0)
        assert blocked["is_paper"] == 1

        executor.reset_cycle_counter()
        after_reset = await executor.execute(_market("m3"), direction="yes", bet_size=3.0)
        assert after_reset["is_paper"] == 0


@pytest.mark.asyncio
async def test_live_api_failure_recorded_as_unconfirmed_live():
    """If place_bet raises, the bet state is UNKNOWN — Manifold may have
    executed it and only the response was lost. Conservative accounting:
    record as live (is_paper=0) with no bet id, so it counts against the
    daily cap and is reconcilable against the Manifold UI. Never downgrade
    a possibly-real bet to paper."""
    fake_client = _FakeManifoldClient(raise_exc=RuntimeError("Manifold returned 503"))
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=3.0)

    assert trade is not None
    assert trade["is_paper"] == 0          # unknown state counts as live
    assert trade["live_bet_id"] is None    # but with no confirmed bet id
    # And it consumes cycle-cap headroom (conservative)
    assert executor.cycle_live_count == 1


@pytest.mark.asyncio
async def test_cap_blocked_bet_still_downgrades_to_paper():
    """Cap blocks happen BEFORE the API call — the bet definitively was not
    placed, so paper downgrade is correct there (unlike API failures)."""
    fake_client = _FakeManifoldClient()

    async def at_cap() -> int:
        return 20

    with patch("src.trading.executor.settings", _mock_settings(live_max_bets_per_day=20)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False,
            manifold_client=fake_client, daily_live_count_provider=at_cap,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=3.0)

    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []  # never reached the API


@pytest.mark.asyncio
async def test_live_bet_rounds_to_integer_mana():
    """Manifold expects integer mana; small floats round correctly."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(live_max_bet_mana=10)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        await executor.execute(_market(), direction="yes", bet_size=3.7)

    assert fake_client.calls[0]["amount"] == 4  # round(3.7) = 4


@pytest.mark.asyncio
async def test_live_bet_below_one_mana_skipped():
    """A bet that rounds to 0 should be skipped entirely (no API call, no record)."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        trade = await executor.execute(_market(), direction="yes", bet_size=0.3)

    assert trade is None
    assert fake_client.calls == []


# ── Close-time horizon tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_skipped_when_market_closes_too_far_out():
    """A market closing 200 days from now is outside the 90-day live horizon."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(live_max_days_to_close=90)):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        far_market = _market(close_in_days=200)
        trade = await executor.execute(far_market, direction="yes", bet_size=3.0)
    assert trade is not None
    assert trade["is_paper"] == 1  # downgraded to paper
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_live_proceeds_when_market_closes_within_horizon():
    """A market closing 30 days from now is well inside the 90-day horizon."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        trade = await executor.execute(_market(close_in_days=30), direction="yes", bet_size=3.0)
    assert trade is not None
    assert trade["is_paper"] == 0
    assert len(fake_client.calls) == 1


@pytest.mark.asyncio
async def test_live_skipped_when_market_has_no_close_time():
    """Markets without closeTime are treated as outside the horizon (conservative)."""
    fake_client = _FakeManifoldClient()
    market_no_close = {"id": "mkt_unknown", "question": "Q?", "probability": 0.6}
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        trade = await executor.execute(market_no_close, direction="yes", bet_size=3.0)
    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_horizon_does_not_affect_paper_mode():
    """Paper mode shouldn't care about closeTime — calibration still flows."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=True, manifold_client=fake_client,
        )
        # Long-horizon market — should still produce a paper trade
        far_market = _market(close_in_days=365)
        trade = await executor.execute(far_market, direction="yes", bet_size=3.0)
    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []


# ── Review-batch regression tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_live_skipped_when_market_already_closed():
    """Horizon check must have a lower bound: a market whose closeTime is in
    the past is not 'within horizon' — betting into it is pure noise."""
    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings()):
        executor = TradeExecutor(
            guardian=_FakeGuardian(), paper_mode=False, manifold_client=fake_client,
        )
        closed_market = _market(close_in_days=-2)  # closed two days ago
        trade = await executor.execute(closed_market, direction="yes", bet_size=3.0)
    assert trade is not None
    assert trade["is_paper"] == 1
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_guardian_records_post_cap_size():
    """The budget guardian must be charged the capped amount actually risked,
    not the raw Kelly suggestion."""
    recorded: list[float] = []

    class _RecordingGuardian:
        def check_and_record(self, amount: float) -> None:
            recorded.append(amount)

    fake_client = _FakeManifoldClient()
    with patch("src.trading.executor.settings", _mock_settings(live_max_bet_mana=5)):
        executor = TradeExecutor(
            guardian=_RecordingGuardian(), paper_mode=False,
            manifold_client=fake_client,
        )
        await executor.execute(_market(), direction="yes", bet_size=20.0)

    assert recorded == [5.0]  # capped size, not the requested 20


def test_place_bet_has_no_retry_wrapper():
    """place_bet must never be retried: /bet is a non-idempotent money-moving
    POST. A timeout doesn't mean the bet failed — retrying can place it twice.
    Tenacity attaches a `.retry` attribute to wrapped functions; place_bet
    must not have one (the GET endpoints keep theirs)."""
    from src.markets.manifold import ManifoldClient
    assert not hasattr(ManifoldClient.place_bet, "retry")
    assert hasattr(ManifoldClient.get_market, "retry")     # GETs still retry
    assert hasattr(ManifoldClient.get_markets, "retry")


# Ensure asyncio module is imported (used by tests above for completeness)
_ = asyncio
