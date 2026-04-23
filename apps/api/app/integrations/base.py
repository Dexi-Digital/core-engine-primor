"""Contrato base para adapters de integracao (API, scraping, RPA)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IntegrationClient(ABC):
    """Todos os adapters devem implementar esta interface.

    Decisoes de design:
      - `health_check`: permite um painel de status agregado na UI.
      - `fetch`/`push` sao metodos genericos assincronos para manter o
        contrato simples; cada adapter define o shape real via subclasses.
      - adapters que usam scraping/RPA devem encapsular headless browsers
        no worker (Celery) e expor apenas metodos idempotentes para a API.
    """

    name: str

    @abstractmethod
    async def health_check(self) -> bool: ...

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D401
        raise NotImplementedError

    async def push(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
