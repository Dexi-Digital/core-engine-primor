"""API do modulo juridico (contencioso vindo do EasyJur)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.juridico import service as svc

router = APIRouter()


class ProcessoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    easyjur_id: int
    numero_cnj: str | None
    status: str | None
    area: str | None
    tribunal: str | None
    instancia: str | None
    comarca: str | None
    titulo: str | None
    cliente: str | None
    contrario: str | None
    tipo_acao: str | None
    risco: str | None
    fase_atual: str | None
    resultado: str | None
    codigo_obra: str | None
    obra_id: int | None


class ProcessoList(BaseModel):
    total: int
    data: list[ProcessoRead]


class AndamentoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_cnj: str
    processo_id: int | None
    tipo: str | None
    status: str | None
    descricao: str | None
    data: date | None


class SyncResponse(BaseModel):
    status: str
    mensagem: str


@router.get("/processos", response_model=ProcessoList)
async def listar_processos(
    status: str | None = Query(None, max_length=64),
    area: str | None = Query(None, max_length=64),
    busca: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ProcessoList:
    linhas, total = await svc.listar_processos(
        db,
        status=status,
        area=area,
        busca=busca,
        limite=page_size,
        offset=(page - 1) * page_size,
    )
    return ProcessoList(
        total=total, data=[ProcessoRead.model_validate(p) for p in linhas]
    )


@router.get("/andamentos", response_model=list[AndamentoRead])
async def ultimos_andamentos(
    limite: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AndamentoRead]:
    return [
        AndamentoRead.model_validate(a)
        for a in await svc.ultimos_andamentos(db, limite=limite)
    ]


@router.get("/resumo", response_model=dict)
async def resumo(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Totais da carteira. Campos esparsos vem SEMPRE com o denominador."""
    return await svc.resumo(db)


@router.post("/sync", response_model=SyncResponse, status_code=202)
async def sincronizar_agora(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SyncResponse:
    """Enfileira a carga no worker e responde na hora.

    Nao roda o pull aqui: sao ~2,5 min contra o EasyJur, e o proxy do
    Railway cortaria a requisicao no meio -- com a carga seguindo ou
    nao, sem ninguem saber. O estado fica no log e a tela acompanha.
    """
    settings = get_settings()
    if not (settings.easyjur_email and settings.easyjur_password):
        await svc.marcar_erro(
            db,
            source="manual",
            mensagem="EASYJUR_EMAIL/EASYJUR_PASSWORD nao configurados neste ambiente",
        )
        raise HTTPException(
            status_code=503,
            detail="EASYJUR_EMAIL/EASYJUR_PASSWORD nao configurados neste ambiente.",
        )
    try:
        await svc.enfileirar_carga(db, source="manual")
    except svc.CargaJaEmAndamento as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except svc.FilaIndisponivel as exc:
        raise HTTPException(
            status_code=503, detail=f"Fila do worker indisponivel: {exc}"
        ) from exc
    return SyncResponse(
        status="em_andamento",
        mensagem="Carga enfileirada. Leva alguns minutos; a tela atualiza sozinha.",
    )
