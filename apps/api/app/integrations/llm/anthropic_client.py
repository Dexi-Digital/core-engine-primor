"""Anthropic Messages API adapter for structured edital analysis (D.5).

Uses the tool_use escape hatch to force a schema-conforming JSON output:
we declare a single tool (`extract_edital`) whose `input_schema` matches
the caller's schema. Claude then returns a `tool_use` block whose `input`
is already a valid dict -- no fragile "parse this json out of prose".

Pricing (as of 2025-Q2, see https://www.anthropic.com/pricing):
    claude-3-5-haiku  $0.80 / MTok input, $4.00 / MTok output
    claude-sonnet-4   $3.00 / MTok input, $15.00 / MTok output
We default to Haiku because D.5 is pure extraction.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.integrations.base import IntegrationClient
from app.integrations.llm.base import (
    LLMError,
    LLMResult,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider(IntegrationClient):
    """LLMProvider backed by Anthropic's Messages API."""

    name = "anthropic"
    # Keep the output budget well above what extract_edital needs so the
    # tool_use block is never truncated. 2048 is more than enough for
    # the current schema (~15 fields).
    max_tokens_output = 2048

    # Default pricing per 1M tokens (USD). Override in subclasses for
    # larger models.
    input_price_per_mtok = 0.80
    output_price_per_mtok = 4.00

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-3-5-haiku-20241022",
        base_url: str = ANTHROPIC_API_BASE,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError("ANTHROPIC_API_KEY ausente")
        self.api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health_check(self) -> bool:
        # Anthropic has no cheap healthcheck endpoint; surface "configured".
        return bool(self.api_key)

    async def analyze(
        self,
        *,
        text: str,
        schema: dict[str, Any],
        system_prompt: str,
    ) -> LLMResult:
        tool = {
            "name": "extract_edital",
            "description": "Retorna a analise estruturada do edital.",
            "input_schema": schema,
        }
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens_output,
            "system": system_prompt,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "extract_edital"},
            "messages": [{"role": "user", "content": text}],
        }

        try:
            resp = await self._client.post("/v1/messages", json=body)
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            raise LLMError(f"anthropic request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"anthropic returned {resp.status_code}: {resp.text[:500]}"
            )

        payload = resp.json()
        tool_input = _extract_tool_input(payload)
        usage = payload.get("usage") or {}
        pt = int(usage.get("input_tokens") or 0)
        ct = int(usage.get("output_tokens") or 0)
        return LLMResult(
            data=tool_input,
            provider=self.name,
            model=self.model,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=self._estimate_cost(pt, ct),
            raw=payload,
        )

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens * self.input_price_per_mtok / 1_000_000)
            + (output_tokens * self.output_price_per_mtok / 1_000_000),
            6,
        )


def _extract_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the tool_use block out of an Anthropic Messages response."""
    content = payload.get("content") or []
    for block in content:
        if block.get("type") == "tool_use":
            data = block.get("input")
            if isinstance(data, dict):
                return data
    raise LLMError("anthropic response has no tool_use block")
