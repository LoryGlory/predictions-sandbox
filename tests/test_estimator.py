"""Unit tests for prompt building and response parsing.

No API calls — we test _parse_response directly and the prompt builder.
"""
import json

import pytest

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
