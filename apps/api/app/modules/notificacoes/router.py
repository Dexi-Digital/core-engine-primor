"""API da caixa de notificacoes.

O destinatario NUNCA vem do cliente -- sai sempre do JWT. Aceitar
`?destinatario=` deixaria qualquer usuario logado ler a caixa de
qualquer outro trocando um parametro na URL.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.notificacoes import service as svc

router = APIRouter()


class NotificacaoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    categoria: str
    titulo: str
    corpo: str
    link: str | None
    lida_em: datetime | None
    created_at: datetime


class NotificacaoList(BaseModel):
    total_nao_lidas: int
    data: list[NotificacaoRead]


class ContadorResponse(BaseModel):
    total: int


@router.get("/notificacoes", response_model=NotificacaoList)
async def listar_notificacoes(
    apenas_nao_lidas: bool = Query(False),
    limite: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificacaoList:
    itens = await svc.listar(
        db,
        destinatario=current_user.email,
        apenas_nao_lidas=apenas_nao_lidas,
        limite=limite,
    )
    return NotificacaoList(
        total_nao_lidas=await svc.contar_nao_lidas(
            db, destinatario=current_user.email
        ),
        data=[NotificacaoRead.model_validate(n) for n in itens],
    )


@router.get("/notificacoes/nao-lidas", response_model=ContadorResponse)
async def contador(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContadorResponse:
    """Alimenta o badge do sino. Barato de proposito: so um COUNT."""
    return ContadorResponse(
        total=await svc.contar_nao_lidas(db, destinatario=current_user.email)
    )


@router.post("/notificacoes/{notificacao_id}/lida", response_model=ContadorResponse)
async def marcar_lida(
    notificacao_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContadorResponse:
    ok = await svc.marcar_lida(
        db, destinatario=current_user.email, notificacao_id=notificacao_id
    )
    if not ok:
        # 404 tambem quando a notificacao existe mas e de outra pessoa:
        # um 403 confirmaria que aquele id existe.
        raise HTTPException(status_code=404, detail="Notificacao nao encontrada")
    return ContadorResponse(
        total=await svc.contar_nao_lidas(db, destinatario=current_user.email)
    )


@router.post("/notificacoes/lidas", response_model=ContadorResponse)
async def marcar_todas_lidas(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContadorResponse:
    await svc.marcar_todas_lidas(db, destinatario=current_user.email)
    return ContadorResponse(total=0)
