"""Kelly Criterion calculator for prediction market bet sizing.

The Kelly formula for a binary bet:
    f* = (bp - q) / b
where:
    p = estimated probability of the outcome we're betting on (YES)
    q = 1 - p
    b = net odds = (1 - market_price) / market_price

Always use fractional Kelly (multiply by 0.25 or similar) to reduce variance.
Cap position size to max_position_pct of bankroll as a second safety net.
"""


def kelly_fraction(
    our_prob: float,
    market_price: float,
    clamp: bool = False,
) -> float:
    """Return the raw Kelly fraction f* for a YES bet.

    Args:
        our_prob: Our estimated probability (0.0–1.0) that the market resolves YES.
        market_price: Current market price for YES (0.0–1.0).
        clamp: If True, return 0.0 instead of a negative value (no-bet signal).

    Returns:
        Kelly fraction. Negative means no edge (don't bet).
    """
    if market_price <= 0 or market_price >= 1:
        raise ValueError(f"market_price must be in (0, 1), got {market_price}")
    if not 0 <= our_prob <= 1:
        raise ValueError(f"our_prob must be in [0, 1], got {our_prob}")

    b = (1 - market_price) / market_price  # net odds
    p = our_prob
    q = 1 - p

    f_star = (b * p - q) / b

    if clamp:
        return max(0.0, f_star)
    return f_star


def kelly_bet_size(
    our_prob: float,
    market_price: float,
    bankroll: float,
    kelly_fraction_multiplier: float = 0.25,
    max_position_pct: float = 0.05,
) -> float:
    """Return the dollar/mana amount to bet, applying fractional Kelly and a position cap.

    Args:
        our_prob: Our estimated YES probability.
        market_price: Current market YES price.
        bankroll: Total capital available.
        kelly_fraction_multiplier: Scale factor (0.25 = quarter Kelly). Conservative.
        max_position_pct: Hard cap — never risk more than this fraction of bankroll.

    Returns:
        Bet size in the same units as bankroll. Zero means no bet.
    """
    f_star = kelly_fraction(our_prob, market_price, clamp=True)
    if f_star == 0.0:
        return 0.0

    fractional = f_star * kelly_fraction_multiplier
    max_bet = bankroll * max_position_pct
    return min(fractional * bankroll, max_bet)


_CONFIDENCE_SCALE = {
    "high": 1.0,
    "medium": 1.0,
    "low": 1.0,
}


def confidence_scale(confidence: str | None) -> float:
    """Map Claude's self-reported confidence to a bet-size multiplier.

    Currently uniform (1.0 across all confidence levels). Earlier analysis on
    609 samples suggested inverting the weights (high=0, medium=1, low=0.3)
    because high-confidence predictions had catastrophic skill scores. On a
    larger sample (910 resolved), the P&L simulation showed uniform weighting
    outperforming the inverted scheme — inverted skipped some profitable
    bets along with the bad ones. See scripts/analyze_confidence.py.

    Wiring kept in place so we can experiment with different weights later
    without touching the pipeline call sites.
    """
    if not confidence:
        return _CONFIDENCE_SCALE["medium"]
    return _CONFIDENCE_SCALE.get(confidence.lower().strip(), _CONFIDENCE_SCALE["medium"])
