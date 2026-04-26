"""Pydantic schemas para o router de OneDrive sync (D1 fase 2)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.onedrive_sync.models import SCOPE_ALL, SCOPES_VALIDOS


class OneDriveSyncRunRequest(BaseModel):
    scope: str = Field(default=SCOPE_ALL)

    def validated_scope(self) -> str:
        if self.scope not in SCOPES_VALIDOS:
            raise ValueError(
                f"scope invalido. Valores: {sorted(SCOPES_VALIDOS)}"
            )
        return self.scope


class OneDriveSyncRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    scope: str | None = None
    triggered_by: str | None = None
    root_folder: str | None = None
    files_scanned: int
    docs_created: int
    docs_updated: int
    docs_skipped: int
    errors_count: int
    summary_json: dict[str, Any] | None = None
    error_message: str | None = None
