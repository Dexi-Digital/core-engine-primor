"""Adapter stub for onsafety. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class OnsafetyClient(IntegrationClient):
    name = "onsafety"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
