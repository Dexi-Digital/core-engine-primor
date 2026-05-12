"""Task Celery para rodar o diagnostico de pastas OneDrive.

POC: roda semanalmente (seg 05:00 America/Sao_Paulo) e loga o resumo.
Futuro (nao neste PR): persistir snapshot numa tabela `onedrive_diagnostico_runs`
+ alerta por email quando `faltando` ou `entidade_fantasma` ficarem > 0.

Agendamento configurado em `worker.main.celery_app.conf.beat_schedule`.
"""
from __future__ import annotations

import asyncio
import os

import structlog

from worker.main import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="worker.tasks.onedrive_diagnostico.run_diagnostico")
def run_diagnostico_task(area: str = "all") -> dict[str, object]:
    """Entry point Celery. Area: all | dp | frota | obras | empresa."""
    return asyncio.run(_run(area))


async def _run(area: str) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.onedrive.client import (
            OneDriveMockClient,
            build_onedrive_client,
        )
        from app.modules.onedrive_diagnostico.service import run_diagnostico
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.ms_graph_root_folder,
        )
    else:
        client = OneDriveMockClient(root_folder=settings.ms_graph_root_folder)

    try:
        async with SessionLocal() as db:
            result = await run_diagnostico(db, client=client, area=area)
    finally:
        await client.aclose()

    # POC: apenas loga. PR seguinte persiste em tabela e dispara alerta.
    logger.info(
        "onedrive_diagnostico.completed",
        root_folder=result.root_folder,
        total_files=result.total_files,
        counts=result.counts,
        area=area,
        env=os.getenv("ENVIRONMENT", "dev"),
    )
    return {
        "root_folder": result.root_folder,
        "total_files": result.total_files,
        "counts": result.counts,
    }
