"""Modulo C - Financeiro e Contratos.

Rotas do ciclo de contratos (Squad 5, demanda #12) sob
`/api/v1/financeiro/contratos`. Assinatura digital e integracao
EasyJur real ficam fora (ver models.py).

Ordem de rotas: estaticas (`/contratos/dispatch-alerts`, Task 5) ANTES
de `/contratos/{contrato_id}` -- mesmo racional do D.6.
"""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.dp_sesmt.schemas import ModuleStatus
from app.modules.financeiro_contratos.schemas import (
    ContratoCreate,
    ContratoRead,
    ContratoUpdate,
)
from app.modules.financeiro_contratos.service import (
    compute_vencimento_status,
    create_contrato,
    delete_contrato,
    get_contrato,
    list_contratos,
    update_contrato,
)

router = APIRouter()


def _contrato_to_read(contrato, *, today: _date | None = None) -> ContratoRead:
    """Hidrata a view com os computados de vencimento (padrao D.6)."""
    today = today or _date.today()
    payload = ContratoRead.model_validate(contrato)
    payload.vencimento_status = compute_vencimento_status(
        contrato.data_fim, today=today
    )
    if contrato.data_fim is not None:
        payload.dias_para_vencer = (contrato.data_fim - today).days
    return payload


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="financeiro_contratos", implemented=True)


@router.get("/contratos", response_model=list[ContratoRead])
async def list_contratos_endpoint(
    status: str | None = Query(None, max_length=32),
    tipo: str | None = Query(None, max_length=32),
    obra_id: int | None = Query(None),
    vence_em_dias: int | None = Query(
        None, ge=0, le=365,
        description="Somente contratos com data_fim entre hoje e hoje+N dias",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[ContratoRead]:
    rows = await list_contratos(
        db, status=status, tipo=tipo, obra_id=obra_id, vence_em_dias=vence_em_dias
    )
    today = _date.today()
    return [_contrato_to_read(r, today=today) for r in rows]


@router.post("/contratos", response_model=ContratoRead, status_code=201)
async def create_contrato_endpoint(
    payload: ContratoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    try:
        row = await create_contrato(
            db,
            titulo=payload.titulo,
            contraparte_nome=payload.contraparte_nome,
            contraparte_documento=payload.contraparte_documento,
            tipo=payload.tipo,
            obra_id=payload.obra_id,
            valor=payload.valor,
            data_inicio=payload.data_inicio,
            data_fim=payload.data_fim,
            status=payload.status,
            easyjur_ref=payload.easyjur_ref,
            observacoes=payload.observacoes,
            actor=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _contrato_to_read(row)


@router.get("/contratos/{contrato_id}", response_model=ContratoRead)
async def get_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
) -> ContratoRead:
    row = await get_contrato(db, contrato_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.patch("/contratos/{contrato_id}", response_model=ContratoRead)
async def update_contrato_endpoint(
    contrato_id: int,
    payload: ContratoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    fields = payload.model_dump(exclude_unset=True)
    try:
        row = await update_contrato(
            db, contrato_id, actor=current_user.email, **fields
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.delete("/contratos/{contrato_id}", status_code=204)
async def delete_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_contrato(db, contrato_id, actor=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
