"""Adapter stub for dominio. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class DominioClient(IntegrationClient):
    name = "dominio"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
