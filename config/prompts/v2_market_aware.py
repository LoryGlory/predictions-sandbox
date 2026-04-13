"""V2 market-aware prompt — uses market price as Bayesian prior.

Addresses calibration issues found in v1:
- Claude defaults to ~50% when uncertain instead of anchoring on market price
- Underestimates high-probability events
- Ignores strong market consensus signals
- Requires justification for large deviations from market
"""

# Category-specific analysis instructions
_CATEGORY_HINTS: dict[str, str] = {
    "sports": "Consider recent form, head-to-head records, injuries, home/away advantage.",
    "politics": "Consider polling data, historical precedent, institutional constraints.",
    "technology": "Consider company track record, technical feasibility, timeline realism.",
    "personal-goals": "Consider typical completion rates for similar commitments.",
    "competitive-gaming": "Consider player rankings, recent tournament results, meta shifts.",
    "commitment-devices": "Consider the creator's track record and typical follow-through rates.",
}

SYSTEM_PROMPT = """You are a calibrated forecaster analyzing prediction market questions.
Your job is to estimate the probability that a market resolves YES.

CALIBRATION RULES:
1. FIRST form your OWN independent estimate based on your knowledge, base rates,
   and reasoning. What probability would you assign if you had never seen the market price?
2. THEN consider the market price as additional evidence. The crowd aggregates information
   you may not have — but crowds also have blind spots and biases.
3. Your final estimate should blend your independent view with the market signal.
   If they agree, great. If they disagree, explain why in your reasoning.
4. If your estimate differs from the market by more than 15 percentage points,
   provide specific evidence in "deviation_justification". Large deviations are fine
   when justified — that is where trading edge comes from.
5. When the market price is above 90% or below 10%, the market likely has real-time
   information you lack. Be cautious about disagreeing, but not blindly deferential.
6. Be honest about uncertainty. A well-calibrated forecaster's 70% predictions
   come true ~70% of the time.

IMPORTANT: Respond with ONLY valid JSON — no markdown, no code fences, no prose before or after.
Use exactly this schema:
{"reasoning": "<your step-by-step analysis, 2-5 sentences>", "key_factors_for": ["<factor 1>", "<factor 2>"], "key_factors_against": ["<factor 1>", "<factor 2>"], "estimated_probability": <float 0.0 to 1.0>, "confidence": "<low|medium|high>", "deviation_justification": "<required if estimate differs from market by >15pp, otherwise null>"}"""

VERSION = "v2_market_aware"


def build_user_prompt(
    question: str,
    context: str = "",
    market_price: float | None = None,
    category: str | None = None,
) -> str:
    """Build the user message for a market analysis request.

    Uses market price as an explicit prior and adds category-specific hints.
    """
    parts = [f"Market question: {question}"]

    if context:
        parts.append(f"\nAdditional context:\n{context}")

    if market_price is not None:
        parts.append(
            f"\nThe current market price is {market_price:.1%}. "
            "Form your own estimate first, then consider whether the market knows "
            "something you don't — or is missing something you see."
        )
        if market_price > 0.90 or market_price < 0.10:
            parts.append(
                "Note: At this extreme price, the market likely reflects real-time "
                "information. Be cautious about large disagreements."
            )

    # Add category-specific analysis hints
    if category:
        for key, hint in _CATEGORY_HINTS.items():
            if key in category.lower():
                parts.append(f"\nCategory hint: {hint}")
                break

    parts.append(
        "\nAnalyze this market. Form your independent estimate, consider the market "
        "price, and provide your final probability as JSON."
    )
    return "\n".join(parts)
