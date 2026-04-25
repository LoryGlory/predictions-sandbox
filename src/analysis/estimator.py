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
    used_web_search: bool = False
    # Populated when estimate_ensemble() is used — list of per-sample dicts
    # like {"temperature": 0.3, "estimated_probability": 0.65, "confidence": "medium"}.
    # None for single-call estimates.
    ensemble_samples: list[dict[str, Any]] | None = None


# Confidence ordering used by the ensemble to pick the most conservative value.
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


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
    def _call_api(
        self,
        model: str,
        max_tokens: int,
        system: str,
        messages: Any,
        use_search: bool = False,
        temperature: float | None = None,
    ) -> tuple[str, bool]:
        """Call Claude API with retry. Returns (raw_text, used_web_search)."""
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if use_search:
            kwargs["tools"] = [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }]

        response = self._client.messages.create(**kwargs)

        # When tool use is enabled, response.content can have multiple blocks
        # (tool_use, tool_result, final text). We need the last TextBlock.
        text_blocks = [
            b for b in response.content if isinstance(b, anthropic.types.TextBlock)
        ]
        if not text_blocks:
            raise ValueError(f"No text block in response: {response.content}")

        # Detect whether search was actually invoked
        used_search = any(
            getattr(b, "type", None) in ("server_tool_use", "web_search_tool_result")
            for b in response.content
        )

        return text_blocks[-1].text, used_search

    async def estimate(
        self,
        question: str,
        context: str = "",
        market_price: float | None = None,
        category: str | None = None,
        prompt_version: str | None = None,
        use_search: bool = False,
        temperature: float | None = None,
    ) -> ProbabilityEstimate:
        """Get a probability estimate from Claude for a market question.

        Args:
            question: The market question.
            context: Optional additional context.
            market_price: Current market price (shown to Claude for reference).
            category: Market category (used for category-specific hints in v2).
            prompt_version: Override prompt version (for A/B testing).
            use_search: If True, enable Anthropic's web_search tool so Claude
                can fetch current information before estimating.
            temperature: Optional sampling temperature. When None, uses the
                Anthropic default. Used by estimate_ensemble() to vary samples.

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

        # Anthropic SDK is sync; wrap in asyncio.to_thread for async pipelines.
        # max_tokens=4096 gives Claude enough headroom for chain-of-thought
        # reasoning plus the final JSON. Sonnet 4.6 doesn't support assistant
        # prefill, so we rely on max_tokens + prompt enforcement.
        raw, used_search = await asyncio.to_thread(
            self._call_api,
            model=self._model,
            max_tokens=4096,
            system=prompt_mod.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            use_search=use_search,
            temperature=temperature,
        )
        result = self._parse_response(raw, model=self._model)
        result.prompt_version = prompt_mod.VERSION
        result.used_web_search = used_search
        return result

    async def estimate_ensemble(
        self,
        question: str,
        context: str = "",
        market_price: float | None = None,
        category: str | None = None,
        prompt_version: str | None = None,
        use_search: bool = False,
        temperatures: list[float] | None = None,
    ) -> ProbabilityEstimate:
        """Run N Claude estimates in parallel at different temperatures and combine.

        Combining rules:
        - estimated_probability: mean of the samples
        - confidence: the LOWEST of the samples (conservative)
        - used_web_search: True if any sample used it
        - reasoning / factors / raw_response: taken from the middle-temperature
          sample (usually the most balanced)
        - ensemble_samples: list of per-sample dicts for later analysis

        All other kwargs are forwarded to estimate() unchanged. The ensemble is
        homogeneous — same model, same prompt version — varied only by sampling
        temperature.
        """
        temps = temperatures or [0.3, 0.7, 1.0]

        results = await asyncio.gather(*[
            self.estimate(
                question=question, context=context, market_price=market_price,
                category=category, prompt_version=prompt_version,
                use_search=use_search, temperature=t,
            )
            for t in temps
        ])

        probs = [r.estimated_probability for r in results]
        mean_prob = sum(probs) / len(probs)

        lowest_conf = min(results, key=lambda r: _CONFIDENCE_RANK.get(r.confidence, 0)).confidence

        # Pick the middle sample (by temperature) as the representative for
        # reasoning/factors — usually the most balanced.
        mid_idx = len(results) // 2
        mid = results[mid_idx]

        samples = [
            {
                "temperature": t,
                "estimated_probability": r.estimated_probability,
                "confidence": r.confidence,
            }
            for t, r in zip(temps, results, strict=True)
        ]

        return ProbabilityEstimate(
            estimated_probability=mean_prob,
            confidence=lowest_conf,
            reasoning=f"[ensemble of {len(results)}] {mid.reasoning}",
            factors_for=mid.factors_for,
            factors_against=mid.factors_against,
            model=mid.model,
            raw_response=mid.raw_response,
            prompt_version=mid.prompt_version,
            used_web_search=any(r.used_web_search for r in results),
            ensemble_samples=samples,
        )

    def should_ab_test(self) -> bool:
        """Return True if this market should get both prompt versions."""
        return random.random() < AB_TEST_RATE

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Strip markdown code fences and prose to extract JSON.

        Claude sometimes writes chain-of-thought prose followed by the JSON
        output. We look for code-fenced JSON first, then fall back to the
        LAST JSON object in the raw text (last match because CoT prose earlier
        in the response might mention `estimated_probability` in plain English).
        """
        import re
        # Try to extract JSON from ```json ... ``` blocks
        match = re.search(r"```(?:json)?\s*\n?(\{[^`]*\})\s*\n?```", raw, re.DOTALL)
        if match:
            return match.group(1)
        # Find the LAST JSON object containing "estimated_probability"
        matches = re.findall(
            r"\{[^{}]*\"estimated_probability\"[^{}]*\}", raw, re.DOTALL,
        )
        if matches:
            return matches[-1]
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
