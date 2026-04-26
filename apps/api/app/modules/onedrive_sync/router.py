"""HTTP router do OneDrive sync (D1 fase 2).

Endpoints:
    POST /api/v1/onedrive-sync/run    -- dispara um run sincrono
    GET  /api/v1/onedrive-sync/runs   -- lista runs (historico)
    GET  /api/v1/onedrive-sync/runs/{id} -- detalhe de 1 run

O run e sincrono dentro do request pra MVP (volumes esperados sao
dezenas a centenas de arquivos, <10s). Quando o volume crescer,
mover pro Celery worker (worker.tasks.onedrive_sync.*) seguindo o
mesmo padrao de `dispatch_aso_alerts`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.integrations.onedrive.client import (
    OneDriveMockClient,
    build_onedrive_client,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.onedrive_sync.models import (
    OneDriveSyncRun,
)
from app.modules.onedrive_sync.schemas import (
    OneDriveSyncRunRead,
    OneDriveSyncRunRequest,
)
from app.modules.onedrive_sync.service import run_sync

router = APIRouter(prefix="/api/v1/onedrive-sync", tags=["onedrive-sync"])


def _get_sync_client():
    """Devolve um client OneDrive para o sync.

    Aproveita as mesmas credenciais `MS_GRAPH_*` do storage (PR #11).
    Se nao houver credenciais, retorna o mock -- comportamento identico
    ao do resto do projeto (DirectData/LLM/OneDrive/Dominio).

    Importante: o `root_folder` aqui e o MESMO do storage principal
    (`ms_graph_root_folder`, default `MotorCentral/editais`). A
    convencao de pastas (dp/, frota/, obras/, empresa/) e aplicada
    DENTRO desse root. Se voce quiser escanear uma pasta diferente,
    mude `MS_GRAPH_ROOT_FOLDER` no env.
    """
    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        return build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.ms_graph_root_folder,
        )
    return OneDriveMockClient(root_folder=settings.ms_graph_root_folder)


@router.post(
    "/run",
    response_model=OneDriveSyncRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def run_endpoint(
    payload: OneDriveSyncRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OneDriveSyncRunRead:
    request = payload or OneDriveSyncRunRequest()
    try:
        scope = request.validated_scope()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    client = _get_sync_client()
    try:
        run = await run_sync(
            db, client=client, scope=scope, actor=current_user.email
        )
    finally:
        await client.aclose()
    return OneDriveSyncRunRead.model_validate(run)


@router.get("/runs", response_model=list[OneDriveSyncRunRead])
async def list_runs_endpoint(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
) -> list[OneDriveSyncRunRead]:
    res = await db.execute(
        select(OneDriveSyncRun)
        .order_by(OneDriveSyncRun.started_at.desc())
        .limit(limit)
    )
    return [
        OneDriveSyncRunRead.model_validate(r) for r in res.scalars().all()
    ]


@router.get("/runs/{run_id}", response_model=OneDriveSyncRunRead)
async def get_run_endpoint(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> OneDriveSyncRunRead:
    res = await db.execute(
        select(OneDriveSyncRun).where(OneDriveSyncRun.id == run_id)
    )
    run = res.scalars().first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run nao encontrado")
    return OneDriveSyncRunRead.model_validate(run)
