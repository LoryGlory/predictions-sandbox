"""Prompt templates for market probability estimation.

Delegates to the active prompt version based on ACTIVE_PROMPT_VERSION config.
Maintains backward compatibility — MARKET_ANALYSIS_SYSTEM and market_analysis_prompt
are still importable from this module.
"""
import logging
from importlib import import_module
from types import ModuleType

from config.settings import settings

logger = logging.getLogger(__name__)

_PROMPT_VERSIONS: dict[str, str] = {
    "v1_baseline": "config.prompts.v1_baseline",
    "v2_market_aware": "config.prompts.v2_market_aware",
}


def _load_prompt_module(version: str) -> ModuleType:
    """Import a prompt version module by name."""
    module_path = _PROMPT_VERSIONS.get(version)
    if not module_path:
        raise ValueError(
            f"Unknown prompt version '{version}'. "
            f"Available: {', '.join(_PROMPT_VERSIONS)}"
        )
    return import_module(module_path)


def get_active_module() -> ModuleType:
    """Return the module for the currently active prompt version."""
    return _load_prompt_module(settings.active_prompt_version)


def get_module_for_version(version: str) -> ModuleType:
    """Return the module for a specific prompt version."""
    return _load_prompt_module(version)


# Backward-compatible exports — point to the active version
_active = get_active_module()
MARKET_ANALYSIS_SYSTEM: str = _active.SYSTEM_PROMPT


def market_analysis_prompt(
    question: str,
    context: str = "",
    market_price: float | None = None,
    category: str | None = None,
) -> str:
    """Build the user message using the active prompt version."""
    return _active.build_user_prompt(question, context, market_price, category)
