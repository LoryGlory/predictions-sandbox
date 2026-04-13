"""Claude probability estimator.

Sends a market question to Claude and parses the structured JSON response
into an estimate we can use for Kelly Criterion calculations.
"""
import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from src.analysis.prompts import get_module_for_version

logger = logging.getLogger(__name__)

# 10% of markets get both v1 and v2 for A/B comparison
AB_TEST_RATE = 0.10


@dataclass
class ProbabilityEstimate:
    estimated_probability: float
    confidence: str          # 'low' | 'medium' | 'high'
    reasoning: str
    factors_for: list[str]
    factors_against: list[str]
    model: str
    raw_response: str        # keep for debugging
    prompt_version: str = ""


class Estimator:
    """Wraps the Anthropic SDK to get probability estimates for market questions."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model or settings.model

    @retry(
        retry=retry_if_exception_type(anthropic.APIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _call_api(self, model: str, max_tokens: int, system: str, messages: Any) -> str:
        """Call Claude API with retry. Returns raw response text."""
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        block = response.content[0]
        if not isinstance(block, anthropic.types.TextBlock):
            raise ValueError(f"Unexpected response block type: {type(block)}")
        return block.text

    async def estimate(
        self,
        question: str,
        context: str = "",
        market_price: float | None = None,
        category: str | None = None,
        prompt_version: str | None = None,
    ) -> ProbabilityEstimate:
        """Get a probability estimate from Claude for a market question.

        Args:
            question: The market question.
            context: Optional additional context.
            market_price: Current market price (shown to Claude for reference).
            category: Market category (used for category-specific hints in v2).
            prompt_version: Override prompt version (for A/B testing).

        Returns:
            Parsed ProbabilityEstimate.

        Raises:
            ValueError: If Claude returns malformed JSON or an out-of-range probability.
        """
        version = prompt_version or settings.active_prompt_version
        prompt_mod = get_module_for_version(version)

        user_message = prompt_mod.build_user_prompt(
            question, context, market_price, category,
        )

        # Anthropic SDK is sync; wrap in asyncio.to_thread for async pipelines
        raw = await asyncio.to_thread(
            self._call_api,
            model=self._model,
            max_tokens=1024,
            system=prompt_mod.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        result = self._parse_response(raw, model=self._model)
        result.prompt_version = prompt_mod.VERSION
        return result

    def should_ab_test(self) -> bool:
        """Return True if this market should get both prompt versions."""
        return random.random() < AB_TEST_RATE

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Strip markdown code fences and leading prose to extract JSON."""
        import re
        # Try to extract JSON from ```json ... ``` blocks
        match = re.search(r"```(?:json)?\s*\n?(\{[^`]*\})\s*\n?```", raw, re.DOTALL)
        if match:
            return match.group(1)
        # Try to find a raw JSON object in the response
        match = re.search(r"\{[^{}]*\"estimated_probability\"[^{}]*\}", raw, re.DOTALL)
        if match:
            return match.group(0)
        return raw

    def _parse_response(self, raw: str, model: str) -> ProbabilityEstimate:
        """Parse Claude's JSON response into a ProbabilityEstimate."""
        cleaned = self._extract_json(raw)
        try:
            data: dict[str, Any] = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude returned non-JSON response: {raw[:200]}") from e

        prob = float(data["estimated_probability"])
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"Probability out of range: {prob}")

        # v2 uses key_factors_for/against, v1 uses factors_for/against
        factors_for = data.get("key_factors_for") or data.get("factors_for", [])
        factors_against = data.get("key_factors_against") or data.get("factors_against", [])

        return ProbabilityEstimate(
            estimated_probability=prob,
            confidence=data.get("confidence", "low"),
            reasoning=data.get("reasoning", ""),
            factors_for=factors_for,
            factors_against=factors_against,
            model=model,
            raw_response=raw,
        )
