"""Shared types for LLM providers (D.5).

A provider turns a free-form text (concatenated edital PDFs) into a
structured JSON payload described by `schema`. The actual wire protocol
(Anthropic tool_use, OpenAI json_schema response format, etc.) is hidden
behind the `LLMProvider` Protocol.

Keeping this tiny:
    - `analyze(text, schema)` is the only required method
    - cost/tokens are reported back so the caller can audit spend
    - `LLMError` / `LLMUnavailableError` let the router distinguish
      "this provider is misconfigured" from "this call failed"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Generic LLM call failure (bad response, rate-limited, parse error)."""


class LLMUnavailableError(LLMError):
    """The provider is not usable in this environment (e.g. no API key).

    Raised by the router to skip over misconfigured providers without
    aborting the whole analysis.
    """


@dataclass(slots=True)
class LLMResult:
    """Structured output of a single LLM call.

    Attributes:
        data:        The structured payload parsed from the provider response.
                     Must already match the schema handed to `analyze()`.
        provider:    Short identifier ("anthropic" / "openai" / "fake").
        model:       Concrete model name (e.g. "claude-3-5-haiku-20241022").
        prompt_tokens / completion_tokens:
                     Token counts reported by the provider (0 if unknown).
        cost_usd:    Estimated cost of the call based on the provider pricing.
                     Useful for auditing in the database.
        raw:         Raw text/JSON the provider returned. Kept for debugging.
    """

    data: dict[str, Any]
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal contract every concrete LLM adapter must fulfil."""

    name: str
    model: str
    # Relative cost in USD per 1M input tokens. The router uses this to
    # order providers when multiple are configured.
    input_price_per_mtok: float

    async def analyze(
        self,
        *,
        text: str,
        schema: dict[str, Any],
        system_prompt: str,
    ) -> LLMResult: ...

    async def aclose(self) -> None: ...
