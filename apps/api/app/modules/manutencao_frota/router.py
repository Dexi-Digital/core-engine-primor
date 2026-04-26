"""Router HTTP do Modulo B (Frota) -- B.1 cadastro + B.3 consulta Detran.

Endpoints:
  - GET    /veiculos                lista com filtros
  - POST   /veiculos                cria
  - GET    /veiculos/{id}           detalhe
  - PATCH  /veiculos/{id}           update parcial
  - DELETE /veiculos/{id}           remove
  - POST   /veiculos/{id}/documentos        adiciona documento
  - PATCH  /veiculos/documentos/{doc_id}    atualiza documento
  - DELETE /veiculos/documentos/{doc_id}    remove documento
  - POST   /veiculos/{id}/consultar-detran  dispara consulta Infosimples (B.3)
  - GET    /veiculos/{id}/consultas         historico de consultas Detran
  - GET    /consultas-detran                lista global (filtros uf/page)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.manutencao_frota import service
from app.modules.manutencao_frota.schemas import (
    ConsultaDetranListResponse,
    ConsultaDetranRead,
    ConsultaDetranRequest,
    DocumentoVeiculoCreate,
    DocumentoVeiculoRead,
    DocumentoVeiculoUpdate,
    ModuleStatus,
    VeiculoCreate,
    VeiculoListResponse,
    VeiculoRead,
    VeiculoUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status", response_model=ModuleStatus)
async def status_endpoint() -> ModuleStatus:
    return ModuleStatus(module="manutencao_frota", implemented=True)


# --- Veiculos ---------------------------------------------------------------


@router.get("/veiculos", response_model=VeiculoListResponse)
async def list_veiculos_endpoint(
    status: str | None = Query(None, max_length=16),
    obra: str | None = Query(None, max_length=128),
    tipo: str | None = Query(None, max_length=32),
    search: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> VeiculoListResponse:
    offset = (page - 1) * page_size
    rows, total = await service.list_veiculos(
        db,
        status=status,
        obra=obra,
        tipo=tipo,
        search=search,
        limit=page_size,
        offset=offset,
    )
    return VeiculoListResponse(
        items=[VeiculoRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/veiculos", response_model=VeiculoRead, status_code=201)
async def create_veiculo_endpoint(
    payload: VeiculoCreate,
    db: AsyncSession = Depends(get_db),
) -> VeiculoRead:
    data = payload.model_dump()
    documentos = data.pop("documentos", None) or None
    veiculo = await service.create_veiculo(
        db,
        documentos=documentos,
        **data,
    )
    return VeiculoRead.model_validate(veiculo)


@router.get("/veiculos/{veiculo_id}", response_model=VeiculoRead)
async def get_veiculo_endpoint(
    veiculo_id: int,
    db: AsyncSession = Depends(get_db),
) -> VeiculoRead:
    row = await service.get_veiculo(db, veiculo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return VeiculoRead.model_validate(row)


@router.patch("/veiculos/{veiculo_id}", response_model=VeiculoRead)
async def update_veiculo_endpoint(
    veiculo_id: int,
    payload: VeiculoUpdate,
    db: AsyncSession = Depends(get_db),
) -> VeiculoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_veiculo(db, veiculo_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return VeiculoRead.model_validate(row)


@router.delete("/veiculos/{veiculo_id}", status_code=204)
async def delete_veiculo_endpoint(
    veiculo_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await service.delete_veiculo(db, veiculo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")


# --- Documentos do veiculo --------------------------------------------------


@router.post(
    "/veiculos/{veiculo_id}/documentos",
    response_model=DocumentoVeiculoRead,
    status_code=201,
)
async def add_documento_endpoint(
    veiculo_id: int,
    payload: DocumentoVeiculoCreate,
    db: AsyncSession = Depends(get_db),
) -> DocumentoVeiculoRead:
    row = await service.add_documento(
        db, veiculo_id, **payload.model_dump()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return DocumentoVeiculoRead.model_validate(row)


@router.patch(
    "/veiculos/documentos/{documento_id}",
    response_model=DocumentoVeiculoRead,
)
async def update_documento_endpoint(
    documento_id: int,
    payload: DocumentoVeiculoUpdate,
    db: AsyncSession = Depends(get_db),
) -> DocumentoVeiculoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_documento(db, documento_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    return DocumentoVeiculoRead.model_validate(row)


@router.delete(
    "/veiculos/documentos/{documento_id}",
    status_code=204,
)
async def delete_documento_endpoint(
    documento_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await service.delete_documento(db, documento_id)
    if not ok:
        raise HTTPException(status_code=404, detail="documento nao encontrado")


# --- B.3 -- Consulta Detran (Infosimples) -----------------------------------


def get_infosimples_dep() -> Any:
    """Dependency override-friendly accessor para o singleton.

    Em testes, basta `app.dependency_overrides[get_infosimples_dep] =
    lambda: FakeClient()` -- sem precisar mexer no `_infosimples_singleton`
    global. Em prod retorna a instancia compartilhada.
    """
    return service.get_infosimples_singleton()


@router.post(
    "/veiculos/{veiculo_id}/consultar-detran",
    response_model=ConsultaDetranRead,
    status_code=201,
)
async def consultar_detran_endpoint(
    veiculo_id: int,
    payload: ConsultaDetranRequest,
    db: AsyncSession = Depends(get_db),
    client: Any = Depends(get_infosimples_dep),
) -> ConsultaDetranRead:
    try:
        consulta = await service.consultar_detran(
            db, veiculo_id, payload.uf, client=client
        )
    except ValueError as exc:
        # `veiculo nao encontrado` ou `uf nao suportada`. UF invalida
        # ja seria pega pelo schema; mantemos defesa-em-profundidade.
        msg = str(exc)
        status = 404 if "veiculo" in msg else 422
        raise HTTPException(status_code=status, detail=msg) from exc
    return ConsultaDetranRead.model_validate(consulta)


@router.get(
    "/veiculos/{veiculo_id}/consultas",
    response_model=ConsultaDetranListResponse,
)
async def list_consultas_veiculo_endpoint(
    veiculo_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ConsultaDetranListResponse:
    veiculo = await service.get_veiculo(db, veiculo_id)
    if veiculo is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    offset = (page - 1) * page_size
    rows, total = await service.list_consultas_detran(
        db, veiculo_id=veiculo_id, limit=page_size, offset=offset
    )
    return ConsultaDetranListResponse(
        items=[ConsultaDetranRead.model_validate(r) for r in rows],
        total=total,
    )


@router.get(
    "/consultas-detran",
    response_model=ConsultaDetranListResponse,
)
async def list_consultas_global_endpoint(
    uf: str | None = Query(None, max_length=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ConsultaDetranListResponse:
    offset = (page - 1) * page_size
    rows, total = await service.list_consultas_detran(
        db, uf=uf, limit=page_size, offset=offset
    )
    return ConsultaDetranListResponse(
        items=[ConsultaDetranRead.model_validate(r) for r in rows],
        total=total,
    )
