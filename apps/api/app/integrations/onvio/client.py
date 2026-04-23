"""Adapter stub for onvio. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class OnvioClient(IntegrationClient):
    name = "onvio"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
