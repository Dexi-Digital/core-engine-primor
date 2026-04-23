"""Adapter stub for onedrive. See docs/integrations.md."""
from __future__ import annotations

from app.integrations.base import IntegrationClient


class OnedriveClient(IntegrationClient):
    name = "onedrive"

    async def health_check(self) -> bool:
        return False  # stub: not implemented yet
