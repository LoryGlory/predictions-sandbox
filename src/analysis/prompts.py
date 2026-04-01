"""Prompt templates for market probability estimation.

Claude is asked to return structured JSON so we can parse the probability
programmatically. The prompt asks for reasoning first, then the estimate,
to encourage chain-of-thought before committing to a number.
"""


MARKET_ANALYSIS_SYSTEM = """You are a calibrated forecaster analyzing prediction market questions.
Your job is to estimate the probability that a market resolves YES.

Be honest about uncertainty. A well-calibrated forecaster's 70% predictions
come true ~70% of the time.

When a current market price is provided:
- If the price is between 10% and 90%, form your own independent estimate. The market can be wrong.
- If the price is above 90% or below 10%, the market almost certainly has real-time information you lack (election results, game scores, confirmed events). Respect that signal heavily — you need strong specific reasoning to disagree with an extreme market price.

IMPORTANT: Respond with ONLY valid JSON — no markdown, no code fences, no prose before or after.
Use exactly this schema:
{"reasoning": "<your step-by-step analysis, 2-5 sentences>", "factors_for": ["<factor 1>", "<factor 2>"], "factors_against": ["<factor 1>", "<factor 2>"], "estimated_probability": <float 0.0 to 1.0>, "confidence": "<low|medium|high>"}"""


def market_analysis_prompt(
    question: str,
    context: str = "",
    market_price: float | None = None,
) -> str:
    """Build the user message for a market analysis request.

    Args:
        question: The market question text.
        context: Optional additional context (description, creator notes, etc.)
        market_price: Current market probability — included for reference but
                      the model is instructed not to anchor on it.
    """
    parts = [f"Market question: {question}"]

    if context:
        parts.append(f"\nAdditional context:\n{context}")

    if market_price is not None:
        if market_price > 0.90 or market_price < 0.10:
            parts.append(
                f"\nCurrent market price: {market_price:.1%} "
                "(EXTREME price — the market likely has information you don't. "
                "Only disagree if you have strong specific reasoning.)"
            )
        else:
            parts.append(
                f"\nCurrent market price: {market_price:.1%} "
                "(for reference — give your independent estimate)"
            )

    parts.append("\nAnalyze this market and provide your probability estimate as JSON.")
    return "\n".join(parts)
