"""Adapter stub for tangerino. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class TangerinoClient(IntegrationClient):
    name = "tangerino"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
