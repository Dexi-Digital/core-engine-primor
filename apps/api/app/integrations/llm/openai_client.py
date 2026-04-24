"""OpenAI Chat Completions adapter for structured edital analysis (D.5).

Uses `response_format.json_schema` with `strict=true` so GPT returns a
valid JSON string that already matches the schema -- no re-prompting.

Pricing (as of 2025-Q2, see https://openai.com/api/pricing):
    gpt-4.1-nano  $0.10 / MTok input, $0.40 / MTok output  (default)
    gpt-4.1-mini  $0.40 / MTok input, $1.60 / MTok output
We pick nano by default: D.5 extracts structured fields, no reasoning.
"""
from __future__ import annotations

import json
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

OPENAI_API_BASE = "https://api.openai.com"


class OpenAIProvider(IntegrationClient):
    """LLMProvider backed by OpenAI Chat Completions."""

    name = "openai"

    input_price_per_mtok = 0.10
    output_price_per_mtok = 0.40

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4.1-nano",
        base_url: str = OPENAI_API_BASE,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError("OPENAI_API_KEY ausente")
        self.api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def analyze(
        self,
        *,
        text: str,
        schema: dict[str, Any],
        system_prompt: str,
    ) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "extract_edital",
                    "schema": schema,
                    "strict": True,
                },
            },
        }

        try:
            resp = await self._client.post("/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            raise LLMError(f"openai request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"openai returned {resp.status_code}: {resp.text[:500]}"
            )

        payload = resp.json()
        content = _extract_chat_content(payload)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"openai response is not valid json: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("openai response is not a json object")

        usage = payload.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        return LLMResult(
            data=data,
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


def _extract_chat_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError("openai response has no choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("openai response has empty content")
    return content
