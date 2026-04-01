"""Prompt templates for market probability estimation.

Claude is asked to return structured JSON so we can parse the probability
programmatically. The prompt asks for reasoning first, then the estimate,
to encourage chain-of-thought before committing to a number.
"""


MARKET_ANALYSIS_SYSTEM = """You are a calibrated forecaster analyzing prediction market questions.
Your job is to estimate the probability that a market resolves YES.

Be honest about uncertainty. A well-calibrated forecaster's 70% predictions
come true ~70% of the time. Do not anchor to the current market price.

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
        parts.append(
            f"\nCurrent market price: {market_price:.1%} "
            "(for reference only — give your independent estimate)"
        )

    parts.append("\nAnalyze this market and provide your probability estimate as JSON.")
    return "\n".join(parts)
