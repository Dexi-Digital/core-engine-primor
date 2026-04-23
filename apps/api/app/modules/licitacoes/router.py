"""Modulo D - Inteligencia de Licitacoes.

Escopo:
    - Scrapers B2G (PNCP primeiro; Conlicitacao, Diarios Oficiais no roadmap).
    - Analise de saude municipal (TCE, transparencia) -- futuro.
    - Busca documental de concorrentes (fase de habilitacao) -- futuro.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.integrations.pncp.client import PncpClient
from app.modules.dp_sesmt.schemas import ModuleStatus
from app.modules.licitacoes.schemas import (
    IngestResult,
    LicitacaoListResponse,
    LicitacaoRead,
)
from app.modules.licitacoes.service import (
    get_licitacao,
    ingest_publicacoes,
    list_licitacoes,
)

router = APIRouter()


def get_pncp_client() -> PncpClient:
    return PncpClient()


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="licitacoes", implemented=True)


@router.get("", response_model=LicitacaoListResponse)
async def list_endpoint(
    uf: str | None = Query(None, max_length=2),
    modalidade: str | None = None,
    orgao_cnpj: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> LicitacaoListResponse:
    items, total = await list_licitacoes(
        db,
        uf=uf,
        modalidade=modalidade,
        orgao_cnpj=orgao_cnpj,
        page=page,
        page_size=page_size,
    )
    return LicitacaoListResponse(
        total=total,
        page=page,
        page_size=page_size,
        data=[LicitacaoRead.model_validate(i) for i in items],
    )


@router.get("/{licitacao_id}", response_model=LicitacaoRead)
async def get_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
) -> LicitacaoRead:
    obj = await get_licitacao(db, licitacao_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Licitacao nao encontrada")
    return LicitacaoRead.model_validate(obj)


@router.post("/ingest/pncp", response_model=IngestResult)
async def ingest_endpoint(
    data_inicial: Annotated[date | None, Query(description="Default: 1 dia atras")] = None,
    data_final: Annotated[date | None, Query(description="Default: hoje")] = None,
    uf: str | None = Query(None, max_length=2),
    max_paginas: int | None = Query(None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    client: PncpClient = Depends(get_pncp_client),
) -> IngestResult:
    """Kick off a PNCP ingestion for the given window.

    Useful for small/on-demand pulls from the UI. Use the Celery task
    `worker.tasks.licitacoes.crawler_pncp` for scheduled / large windows.
    """
    today = date.today()
    data_final = data_final or today
    data_inicial = data_inicial or (data_final - timedelta(days=1))
    if data_inicial > data_final:
        raise HTTPException(status_code=400, detail="data_inicial > data_final")

    try:
        return await ingest_publicacoes(
            db,
            client,
            data_inicial=data_inicial,
            data_final=data_final,
            uf=uf,
            max_paginas=max_paginas,
        )
    finally:
        await client.aclose()
