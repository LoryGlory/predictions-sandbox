"""V3 scenario-decomposition prompt — forces structured pre-commit before probability.

Based on the Metaculus Q1/Q2 template bot pattern (validated by Halawi et al.,
NeurIPS 2024). The hypothesis: forcing Claude to write out four specific
scenarios before producing a probability reduces hindsight bias and base-rate
neglect, both well-documented LLM forecasting failure modes.

Key differences from v2:
- v2 asked for "independent estimate then justify deviation"
- v3 forces explicit STATUS_QUO / NO_SCENARIO / YES_SCENARIO / BASE_RATE
  written as prose BEFORE any number is committed
- The scenario sections become part of reasoning (still parseable as JSON)
"""

# Category-specific analysis instructions (same set as v2, kept verbatim)
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

REQUIRED REASONING STRUCTURE:
Before producing a probability, you MUST write out the following four sections.
Skipping this structure consistently produces worse forecasts.

1. STATUS QUO: What happens if nothing changes between now and resolution?
   The world changes slowly most of the time. For most "Will X happen by date Y"
   markets, the most likely outcome is "no change." Start by considering this.

2. NO SCENARIO: Describe the most plausible specific path to a NO outcome.
   What concrete events / non-events would cause NO? Make it specific.

3. YES SCENARIO: Describe the most plausible specific path to a YES outcome.
   What concrete events would cause YES? Make it specific.

4. BASE RATE: For questions of this structural type, what fraction historically
   resolve YES? Reference comparable past events if you can.

CALIBRATION RULES:
- After writing the four sections, weigh them and produce your probability.
- The market price is information. If your estimate differs from market by more
  than 15 percentage points, provide specific evidence in "deviation_justification".
- When the market price is above 90% or below 10%, the market likely has real-time
  information you lack. Be cautious about large disagreements, but not blindly deferential.
- Well-calibrated forecasters: their 70% predictions come true ~70% of the time.

IMPORTANT: Respond with ONLY valid JSON — no markdown, no code fences, no prose before or after.
Use exactly this schema:
{"scenarios": {"status_quo": "<1-2 sentences>", "no_path": "<1-2 sentences>", "yes_path": "<1-2 sentences>", "base_rate": "<1-2 sentences with comparable past events>"}, "reasoning": "<how you weighed the scenarios, 1-3 sentences>", "key_factors_for": ["<factor 1>", "<factor 2>"], "key_factors_against": ["<factor 1>", "<factor 2>"], "estimated_probability": <float 0.0 to 1.0>, "confidence": "<low|medium|high>", "deviation_justification": "<required if estimate differs from market by >15pp, otherwise null>"}"""

VERSION = "v3_scenario"


def build_user_prompt(
    question: str,
    context: str = "",
    market_price: float | None = None,
    category: str | None = None,
) -> str:
    """Build the user message for a market analysis request.

    The scenario structure is enforced in the system prompt; this user message
    just provides the question and optional market context.
    """
    parts = [f"Market question: {question}"]

    if context:
        parts.append(f"\nAdditional context:\n{context}")

    if market_price is not None:
        parts.append(
            f"\nThe current market price is {market_price:.1%}. "
            "Work through the four scenarios first, THEN consider the market signal."
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
        "\nWork through STATUS_QUO, NO_SCENARIO, YES_SCENARIO, and BASE_RATE in "
        "the JSON `scenarios` object. Then produce your probability."
    )
    return "\n".join(parts)
