"""Cost-routed LLM wrapper.

Given an ordered list of providers, try the cheapest first and fall back
to the next one if the call fails with a transient `LLMError`. A
provider marked `LLMUnavailableError` is skipped (wrong config),
everything else is transparent to the caller.
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.llm.base import (
    LLMError,
    LLMProvider,
    LLMResult,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)


class CostRoutedProvider:
    """Exposes the LLMProvider interface across N underlying providers.

    Providers are tried in ascending `input_price_per_mtok` order, unless
    an explicit order is supplied. The router keeps track of the provider
    that served the call via `LLMResult.provider` so the service layer
    can audit spend per call.
    """

    name = "cost-routed"
    model = "cost-routed"
    input_price_per_mtok = 0.0

    def __init__(
        self,
        providers: list[LLMProvider],
        *,
        sort_by_cost: bool = True,
    ) -> None:
        if not providers:
            raise LLMUnavailableError("nenhum LLMProvider configurado")
        self._providers: list[LLMProvider] = (
            sorted(providers, key=lambda p: p.input_price_per_mtok)
            if sort_by_cost
            else list(providers)
        )

    async def aclose(self) -> None:
        for p in self._providers:
            try:
                await p.aclose()
            except Exception:  # noqa: BLE001 - best effort cleanup
                logger.warning("aclose falhou em provider %s", p.name)

    async def analyze(
        self,
        *,
        text: str,
        schema: dict[str, Any],
        system_prompt: str,
    ) -> LLMResult:
        last_exc: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.analyze(
                    text=text, schema=schema, system_prompt=system_prompt
                )
            except LLMUnavailableError as exc:
                logger.info("llm provider %s indisponivel: %s", provider.name, exc)
                last_exc = exc
                continue
            except LLMError as exc:
                logger.warning(
                    "llm provider %s falhou, tentando proximo: %s",
                    provider.name,
                    exc,
                )
                last_exc = exc
                continue
        raise LLMError(
            f"todos os providers LLM falharam ({len(self._providers)}): {last_exc}"
        )
