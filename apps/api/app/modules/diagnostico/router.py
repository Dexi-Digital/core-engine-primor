"""HTTP router para o motor de Diagnostico Documental (D1)."""
from __future__ import annotations

import csv
import io
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.diagnostico.models import (
    DiagnosticoFinding,
    DiagnosticoRun,
)
from app.modules.diagnostico.runner import run_diagnostico
from app.modules.diagnostico.schemas import (
    DiagnosticoFindingRead,
    DiagnosticoRunDetail,
    DiagnosticoRunRead,
    DiagnosticoRunRequest,
)

router = APIRouter(prefix="/api/v1/diagnostico", tags=["diagnostico"])

VALID_SCOPES = frozenset({"all", "dp", "sst", "frota", "empresa", "obra"})


@router.post(
    "/run",
    response_model=DiagnosticoRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def run_endpoint(
    payload: DiagnosticoRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiagnosticoRunRead:
    """Dispara um run sincrono do diagnostico.

    Operacao roda dentro do request -- aceitavel em volume MVP (algumas
    centenas de funcionarios + dezenas de veiculos + dezenas de obras).
    Volume maior deve ser movido pra Celery (worker task).
    """
    scope = payload.scope if payload else "all"
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"scope invalido. Valores: {sorted(VALID_SCOPES)}",
        )
    run = await run_diagnostico(
        db, scope=scope, actor=current_user.email
    )
    return DiagnosticoRunRead.model_validate(run)


@router.get("/runs", response_model=list[DiagnosticoRunRead])
async def list_runs_endpoint(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
) -> list[DiagnosticoRunRead]:
    res = await db.execute(
        select(DiagnosticoRun)
        .order_by(DiagnosticoRun.started_at.desc())
        .limit(limit)
    )
    return [DiagnosticoRunRead.model_validate(r) for r in res.scalars().all()]


@router.get("/runs/{run_id}", response_model=DiagnosticoRunDetail)
async def get_run_endpoint(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagnosticoRunDetail:
    res = await db.execute(
        select(DiagnosticoRun).where(DiagnosticoRun.id == run_id)
    )
    run = res.scalars().first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run nao encontrado")

    res2 = await db.execute(
        select(DiagnosticoFinding)
        .where(DiagnosticoFinding.run_id == run_id)
        .order_by(
            DiagnosticoFinding.area,
            DiagnosticoFinding.entity_label,
            DiagnosticoFinding.doc_tipo,
        )
    )
    rows = list(res2.scalars().all())

    grouped: dict[str, list[DiagnosticoFindingRead]] = defaultdict(list)
    for r in rows:
        grouped[r.area].append(DiagnosticoFindingRead.model_validate(r))

    return DiagnosticoRunDetail(
        run=DiagnosticoRunRead.model_validate(run),
        findings_by_area=dict(grouped),
    )


@router.get("/runs/{run_id}/export.csv")
async def export_run_csv_endpoint(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Exporta findings de um run em CSV (UTF-8 com BOM para Excel)."""
    res = await db.execute(
        select(DiagnosticoRun).where(DiagnosticoRun.id == run_id)
    )
    if res.scalars().first() is None:
        raise HTTPException(status_code=404, detail="Run nao encontrado")

    res2 = await db.execute(
        select(DiagnosticoFinding)
        .where(DiagnosticoFinding.run_id == run_id)
        .order_by(
            DiagnosticoFinding.area,
            DiagnosticoFinding.entity_label,
            DiagnosticoFinding.doc_tipo,
        )
    )
    rows = list(res2.scalars().all())

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM para Excel
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "area",
            "entity_type",
            "entity_id",
            "entity_label",
            "doc_tipo",
            "doc_label",
            "status",
            "validade",
            "dias_para_vencimento",
            "message",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.area,
                r.entity_type,
                r.entity_id or "",
                r.entity_label,
                r.doc_tipo,
                r.doc_label or "",
                r.status,
                r.validade.isoformat() if r.validade else "",
                r.dias_para_vencimento if r.dias_para_vencimento is not None else "",
                r.message or "",
            ]
        )

    buf.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="diagnostico_run_{run_id}.csv"',
    }
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
