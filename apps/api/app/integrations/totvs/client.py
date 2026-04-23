"""Adapter stub for totvs. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class TotvsClient(IntegrationClient):
    name = "totvs"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
