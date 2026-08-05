"""Fabrica do backend `EditaisStorage` compartilhada entre API e worker.

Antes da Squad 2, a selecao de backend vivia dentro da dependency
FastAPI `get_editais_storage` (router.py) -- o worker Celery nao
conseguia reutiliza-la. Extraida para ca como async context manager:
o `finally` fecha o `httpx.AsyncClient` interno do OneDriveClient
(sem isso, cada uso vaza um pool TCP).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import Settings
from app.integrations.onedrive.client import build_onedrive_client
from app.integrations.onedrive.storage import OneDriveStorage
from app.modules.licitacoes.storage import EditaisStorage, LocalStorage


@asynccontextmanager
async def editais_storage(settings: Settings) -> AsyncIterator[EditaisStorage]:
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.ms_graph_root_folder,
        )
        try:
            yield OneDriveStorage(client)
        finally:
            await client.aclose()
        return
    yield LocalStorage(settings.editais_storage_path)
