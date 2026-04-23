"""Adapter stub for sistema90. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class Sistema90Client(IntegrationClient):
    name = "sistema90"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
