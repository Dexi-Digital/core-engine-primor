"""HTTP router do diagnostico OneDrive.

Endpoints:
    GET /api/v1/diagnostico/onedrive?area=all|dp|frota|obras|empresa

Aproveita o mesmo client-factory do OneDrive sync (PR #26): se
`STORAGE_BACKEND=onedrive` + credenciais `MS_GRAPH_*` setadas, usa o
client real; senao, mock deterministico.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.integrations.onedrive.client import (
    OneDriveMockClient,
    build_onedrive_client,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.onedrive_diagnostico.schemas import DiagnosticoResponse
from app.modules.onedrive_diagnostico.service import run_diagnostico
from app.modules.onedrive_diagnostico.spec import AREAS

router = APIRouter(
    prefix="/api/v1/diagnostico", tags=["diagnostico"]
)


def _get_client():
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


@router.get("/onedrive", response_model=DiagnosticoResponse)
async def diagnostico_onedrive_endpoint(
    area: str = Query(
        default="all",
        description=f"all ou um de: {', '.join(AREAS)}",
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DiagnosticoResponse:
    client = _get_client()
    try:
        try:
            result = await run_diagnostico(db, client=client, area=area)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await client.aclose()
    return DiagnosticoResponse.from_result(result)
