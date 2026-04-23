"""Adapter stub for whatsapp. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class WhatsappClient(IntegrationClient):
    name = "whatsapp"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
