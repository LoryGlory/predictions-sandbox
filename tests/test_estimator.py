"""Unit tests for prompt building and response parsing.

No API calls — we test _parse_response directly and the prompt builder.
"""
import json

import pytest

from config.prompts.v1_baseline import SYSTEM_PROMPT as V1_SYSTEM
from config.prompts.v1_baseline import build_user_prompt as v1_prompt
from config.prompts.v2_market_aware import SYSTEM_PROMPT as V2_SYSTEM
from config.prompts.v2_market_aware import build_user_prompt as v2_prompt
from src.analysis.estimator import Estimator, ProbabilityEstimate
from src.analysis.prompts import MARKET_ANALYSIS_SYSTEM, market_analysis_prompt

# ── Prompt builder tests ────────────────────────────────────────────────────


def test_prompt_includes_question():
    prompt = market_analysis_prompt("Will X happen?")
    assert "Will X happen?" in prompt


def test_prompt_includes_market_price():
    prompt = market_analysis_prompt("Will X?", market_price=0.65)
    assert "65.0%" in prompt


def test_prompt_omits_price_when_none():
    prompt = market_analysis_prompt("Will X?", market_price=None)
    assert "%" not in prompt


def test_prompt_includes_context_when_provided():
    prompt = market_analysis_prompt("Will X?", context="Some background info")
    assert "Some background info" in prompt


def test_prompt_omits_context_section_when_empty():
    prompt = market_analysis_prompt("Will X?", context="")
    assert "Additional context" not in prompt


def test_system_prompt_requests_json():
    assert "JSON" in MARKET_ANALYSIS_SYSTEM
    assert "estimated_probability" in MARKET_ANALYSIS_SYSTEM


# ── Response parser tests ───────────────────────────────────────────────────


def make_estimator():
    return Estimator(api_key="dummy", model="claude-test")


def valid_response(prob=0.72, confidence="medium"):
    return json.dumps({
        "reasoning": "Some analysis",
        "factors_for": ["factor a"],
        "factors_against": ["factor b"],
        "estimated_probability": prob,
        "confidence": confidence,
    })


def test_parse_valid_response():
    e = make_estimator()
    result = e._parse_response(valid_response(), model="claude-test")
    assert isinstance(result, ProbabilityEstimate)
    assert result.estimated_probability == pytest.approx(0.72)
    assert result.confidence == "medium"
    assert result.model == "claude-test"
    assert result.factors_for == ["factor a"]
    assert result.factors_against == ["factor b"]


def test_parse_stores_raw_response():
    e = make_estimator()
    raw = valid_response()
    result = e._parse_response(raw, model="claude-test")
    assert result.raw_response == raw


def test_parse_raises_on_invalid_json():
    e = make_estimator()
    with pytest.raises(ValueError, match="non-JSON"):
        e._parse_response("not json at all", model="claude-test")


def test_parse_raises_on_out_of_range_probability():
    e = make_estimator()
    bad = json.dumps({"estimated_probability": 1.5, "confidence": "high"})
    with pytest.raises(ValueError, match="out of range"):
        e._parse_response(bad, model="claude-test")


def test_parse_uses_defaults_for_missing_fields():
    e = make_estimator()
    minimal = json.dumps({"estimated_probability": 0.5})
    result = e._parse_response(minimal, model="claude-test")
    assert result.confidence == "low"
    assert result.reasoning == ""
    assert result.factors_for == []
    assert result.factors_against == []


# ── JSON extraction tests (handles non-clean Claude responses) ───────────


def test_parse_strips_markdown_code_fences():
    e = make_estimator()
    wrapped = '```json\n' + valid_response() + '\n```'
    result = e._parse_response(wrapped, model="claude-test")
    assert result.estimated_probability == pytest.approx(0.72)


def test_parse_extracts_json_from_prose():
    e = make_estimator()
    prose = 'Here is my analysis:\n\n' + valid_response() + '\n\nHope that helps!'
    result = e._parse_response(prose, model="claude-test")
    assert result.estimated_probability == pytest.approx(0.72)


def test_parse_handles_code_fence_without_json_tag():
    e = make_estimator()
    wrapped = '```\n' + valid_response() + '\n```'
    result = e._parse_response(wrapped, model="claude-test")
    assert result.estimated_probability == pytest.approx(0.72)


# ── V2 prompt tests ───────────────────────────────────────────────────────


def test_v2_system_prompt_requires_json():
    assert "JSON" in V2_SYSTEM
    assert "estimated_probability" in V2_SYSTEM


def test_v2_system_prompt_mentions_independent_estimate():
    assert "independent estimate" in V2_SYSTEM or "independent view" in V2_SYSTEM


def test_v2_system_prompt_requires_deviation_justification():
    assert "deviation_justification" in V2_SYSTEM
    assert "15 percentage points" in V2_SYSTEM or "15pp" in V2_SYSTEM


def test_v2_prompt_includes_market_price():
    prompt = v2_prompt("Will X?", market_price=0.65)
    assert "65.0%" in prompt
    assert "own estimate" in prompt or "market price" in prompt


def test_v2_prompt_warns_on_extreme_price():
    prompt = v2_prompt("Will X?", market_price=0.95)
    assert "extreme" in prompt.lower() or "cautious" in prompt.lower()


def test_v2_prompt_includes_category_hint():
    prompt = v2_prompt("Will X?", category="competitive-gaming")
    assert "player rankings" in prompt or "tournament" in prompt


def test_v2_prompt_no_hint_for_unknown_category():
    prompt = v2_prompt("Will X?", category="obscure-thing")
    assert "Category hint:" not in prompt


def test_v2_parse_accepts_key_factors_fields():
    """V2 returns key_factors_for/against instead of factors_for/against."""
    e = make_estimator()
    v2_response = json.dumps({
        "reasoning": "Analysis here",
        "key_factors_for": ["good factor"],
        "key_factors_against": ["bad factor"],
        "estimated_probability": 0.65,
        "confidence": "medium",
        "deviation_justification": None,
    })
    result = e._parse_response(v2_response, model="claude-test")
    assert result.factors_for == ["good factor"]
    assert result.factors_against == ["bad factor"]


def test_v1_preserved_as_baseline():
    """V1 baseline still works and has the expected structure."""
    assert "JSON" in V1_SYSTEM
    prompt = v1_prompt("Will X?", market_price=0.5)
    assert "Will X?" in prompt


# ── A/B testing ────────────────────────────────────────────────────────────


def test_ab_test_rate_is_reasonable():
    """A/B test should fire on ~10% of calls (statistical test with tolerance)."""
    e = make_estimator()
    hits = sum(1 for _ in range(1000) if e.should_ab_test())
    assert 30 < hits < 200  # ~10% with generous tolerance


def test_prompt_version_stored_on_estimate():
    e = make_estimator()
    result = e._parse_response(valid_response(), model="claude-test")
    # prompt_version defaults to "" when not set via estimate()
    assert result.prompt_version == ""


# ── Web search integration tests ────────────────────────────────────────


def test_used_web_search_defaults_to_false():
    e = make_estimator()
    result = e._parse_response(valid_response(), model="claude-test")
    assert result.used_web_search is False


@pytest.mark.asyncio
async def test_estimate_passes_use_search_flag(monkeypatch):
    """When use_search=True, the tool arg should propagate and flag the result."""
    e = make_estimator()
    captured: dict = {}

    def fake_call_api(model, max_tokens, system, messages, use_search=False):
        captured["use_search"] = use_search
        return valid_response(), use_search  # pretend search was used iff requested

    monkeypatch.setattr(e, "_call_api", fake_call_api)
    result = await e.estimate("Will Iran do X?", use_search=True)
    assert captured["use_search"] is True
    assert result.used_web_search is True


@pytest.mark.asyncio
async def test_estimate_default_does_not_use_search(monkeypatch):
    e = make_estimator()
    captured: dict = {}

    def fake_call_api(model, max_tokens, system, messages, use_search=False):
        captured["use_search"] = use_search
        return valid_response(), False

    monkeypatch.setattr(e, "_call_api", fake_call_api)
    result = await e.estimate("Boring question?")
    assert captured["use_search"] is False
    assert result.used_web_search is False


@pytest.mark.asyncio
async def test_estimate_prefills_opening_brace(monkeypatch):
    """Messages must include an assistant prefill of '{' to force JSON output."""
    e = make_estimator()
    captured: dict = {}

    def fake_call_api(model, max_tokens, system, messages, use_search=False):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        # Simulate Claude continuing from the "{" prefill — response omits opener
        raw_without_opener = valid_response()[1:]  # drop leading "{"
        return raw_without_opener, False

    monkeypatch.setattr(e, "_call_api", fake_call_api)
    result = await e.estimate("Will X happen?")
    # Verify prefill was sent
    assert len(captured["messages"]) == 2
    assert captured["messages"][1] == {"role": "assistant", "content": "{"}
    # Verify max_tokens was raised for longer responses
    assert captured["max_tokens"] >= 2048
    # Verify the prefill is reattached and parsing succeeds
    assert result.estimated_probability == pytest.approx(0.72)
