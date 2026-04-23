"""Adapter stub for easyjur. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class EasyjurClient(IntegrationClient):
    name = "easyjur"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
