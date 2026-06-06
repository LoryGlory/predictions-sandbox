"""Realised P&L computation for settled binary-market trades.

Manifold YES/NO markets: a bet of size X at YES price p effectively buys
X/p YES shares (or X/(1-p) NO shares). On resolution each winning share
pays out 1; losing shares pay 0. The pure-function below codifies that —
no DB, no API.
"""


def compute_pnl(
    direction: str,
    entry_price: float,
    size: float,
    outcome: int,
) -> float:
    """Compute realised P&L for a settled binary-market trade.

    Args:
        direction: "yes" or "no" — the side bet on. Case-insensitive.
        entry_price: Market YES price at bet time, strictly in (0, 1).
        size: Bet amount in mana / dollars.
        outcome: 1 if the market resolved YES, 0 if NO.

    Returns:
        Realised P&L (positive = profit, negative = loss).

    Raises:
        ValueError: if outcome or entry_price is out of range.
    """
    if outcome not in (0, 1):
        raise ValueError(f"outcome must be 0 or 1, got {outcome}")
    if not 0 < entry_price < 1:
        raise ValueError(f"entry_price must be in (0, 1), got {entry_price}")

    direction = direction.lower()
    won = (
        (direction == "yes" and outcome == 1)
        or (direction == "no" and outcome == 0)
    )
    if not won:
        return -size

    # Winning bet: payout is size / effective_price; profit is that minus stake
    effective_price = entry_price if direction == "yes" else (1 - entry_price)
    return size * (1.0 / effective_price - 1.0)
