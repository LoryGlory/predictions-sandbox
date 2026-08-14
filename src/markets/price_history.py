"""Historical price reconstruction for Manifold markets.

WHY THIS EXISTS — read before using /v0/markets `probability` for anything
historical: that field is the CURRENT value. For a resolved market it sits at
roughly 1.0 (YES) or 0.0 (NO), i.e. it *is* the outcome. Bucketing resolved
markets by it and then measuring how often each bucket resolved YES would
produce a spectacular, entirely circular "finding". `fetch_resolved_markets`
in src/backtesting/backtest.py reads that field and its comment calls it "the
probability at resolution time" — true, and useless as a pre-close price.

The honest price at time T is reconstructed from trade history: the `probAfter`
of the last bet strictly before T.

Disk caching is mandatory rather than nice-to-have: a Phase 0 run touches
thousands of markets, and a failure partway through must not re-pull.
"""
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class PriceSnapshot:
    """A reconstructed price, with the provenance needed to audit it."""

    contract_id: str
    cutoff_ms: int
    price: float | None          # None => no trade before the cutoff
    bet_id: str | None
    bet_time_ms: int | None
    pages_fetched: int

    @property
    def usable(self) -> bool:
        return self.price is not None


def cutoff_for_close(close_time_ms: int, hours_before: int) -> int:
    """Cutoff timestamp: `hours_before` hours prior to market close."""
    return close_time_ms - hours_before * MS_PER_HOUR


def last_price_before(
    bets: list[dict[str, Any]],
    cutoff_ms: int,
) -> tuple[float | None, str | None, int | None]:
    """Price implied by the last trade strictly before `cutoff_ms`.

    Returns (probAfter, bet_id, bet_time_ms), or (None, None, None) if this
    batch contains no qualifying trade.

    Ordering is NOT assumed: Manifold returns newest-first, but relying on
    that silently would make the result wrong if the API ever changed. The
    max-by-timestamp is computed explicitly.
    """
    best: tuple[float, str | None, int] | None = None
    for bet in bets:
        created = bet.get("createdTime")
        prob_after = bet.get("probAfter")
        if created is None or prob_after is None:
            continue
        if created >= cutoff_ms:
            continue
        if best is None or created > best[2]:
            best = (float(prob_after), bet.get("id"), int(created))
    if best is None:
        return (None, None, None)
    return best


class PriceHistoryCache:
    """Disk cache of reconstructed snapshots, one JSON file per market.

    Keyed on (contract_id, cutoff hours) so that changing the pre-registered
    cutoff invalidates cleanly instead of silently serving a snapshot taken at
    a different time.
    """

    def __init__(self, root: Path, cutoff_hours: int) -> None:
        self.dir = Path(root) / f"cutoff_{cutoff_hours}h"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, contract_id: str) -> Path:
        # Contract ids are alphanumeric; keep a guard so a surprising id can
        # never escape the cache directory.
        safe = "".join(c for c in contract_id if c.isalnum() or c in "-_")
        return self.dir / f"{safe}.json"

    def get(self, contract_id: str) -> PriceSnapshot | None:
        path = self._path(contract_id)
        if not path.exists():
            return None
        try:
            return PriceSnapshot(**json.loads(path.read_text()))
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.debug("Discarding unreadable cache entry %s", path)
            return None

    def put(self, snapshot: PriceSnapshot) -> None:
        self._path(snapshot.contract_id).write_text(json.dumps(asdict(snapshot)))

    def __len__(self) -> int:
        return sum(1 for _ in self.dir.glob("*.json"))


async def fetch_price_at_cutoff(
    client: Any,
    contract_id: str,
    cutoff_ms: int,
    cache: PriceHistoryCache | None = None,
    max_pages: int = 20,
) -> PriceSnapshot:
    """Reconstruct a market's price at `cutoff_ms`, caching the result.

    Pages backwards through trade history (newest first) and stops as soon as
    a trade older than the cutoff is found — for most markets that is the
    first page, so the common case costs one request.

    Args:
        client: An open ManifoldClient.
        contract_id: Market to reconstruct.
        cutoff_ms: Timestamp to price at.
        cache: Optional disk cache; consulted before any network call.
        max_pages: Safety bound on backwards pagination.
    """
    if cache is not None:
        cached = cache.get(contract_id)
        if cached is not None and cached.cutoff_ms == cutoff_ms:
            return cached

    before: str | None = None
    pages = 0
    snapshot = PriceSnapshot(contract_id, cutoff_ms, None, None, None, 0)

    for _ in range(max_pages):
        batch = await client.get_market_bets(contract_id, limit=1000, before=before)
        pages += 1
        if not batch:
            break

        price, bet_id, bet_time = last_price_before(batch, cutoff_ms)
        if price is not None:
            snapshot = PriceSnapshot(
                contract_id, cutoff_ms, price, bet_id, bet_time, pages,
            )
            break

        # Every trade in this page is at or after the cutoff — page further back.
        oldest = min(
            (b for b in batch if b.get("createdTime") is not None),
            key=lambda b: b["createdTime"],
            default=None,
        )
        if oldest is None or not oldest.get("id"):
            break
        before = oldest["id"]
    else:
        logger.debug("Hit max_pages for %s without reaching the cutoff", contract_id)

    if snapshot.pages_fetched == 0:
        snapshot = PriceSnapshot(contract_id, cutoff_ms, None, None, None, pages)

    if cache is not None:
        cache.put(snapshot)
    return snapshot
