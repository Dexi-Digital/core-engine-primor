"""LLM provider adapters (D.5).

Two concrete adapters (Anthropic + OpenAI) plus a cost-routed wrapper.
The router tries providers in ascending cost order and falls back on
failure, so callers can stay provider-agnostic.
"""
from app.integrations.llm.base import (
    LLMError,
    LLMProvider,
    LLMResult,
    LLMUnavailableError,
)

__all__ = [
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "LLMUnavailableError",
]
