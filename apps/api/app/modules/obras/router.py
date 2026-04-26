"""HTTP router para Obras (D1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.obras import service
from app.modules.obras.schemas import (
    ObraCreate,
    ObraDocumentoCreate,
    ObraDocumentoRead,
    ObraDocumentoUpdate,
    ObraRead,
    ObraUpdate,
)

router = APIRouter(prefix="/api/v1/obras", tags=["obras"])


# --- Obra CRUD ------------------------------------------------------------


@router.post("", response_model=ObraRead, status_code=status.HTTP_201_CREATED)
async def create_obra_endpoint(
    payload: ObraCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObraRead:
    obra = await service.create_obra(db, payload, actor=current_user.email)
    return ObraRead.model_validate(obra)


@router.get("", response_model=list[ObraRead])
async def list_obras_endpoint(
    db: AsyncSession = Depends(get_db),
    obra_status: str | None = None,
    uf: str | None = None,
    limit: int = 200,
) -> list[ObraRead]:
    rows = await service.list_obras(db, status=obra_status, uf=uf, limit=limit)
    return [ObraRead.model_validate(r) for r in rows]


@router.get("/{obra_id}", response_model=ObraRead)
async def get_obra_endpoint(
    obra_id: int, db: AsyncSession = Depends(get_db)
) -> ObraRead:
    obra = await service.get_obra(db, obra_id)
    if obra is None:
        raise HTTPException(status_code=404, detail="Obra nao encontrada")
    return ObraRead.model_validate(obra)


@router.put("/{obra_id}", response_model=ObraRead)
async def update_obra_endpoint(
    obra_id: int,
    payload: ObraUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObraRead:
    obra = await service.update_obra(
        db, obra_id, payload, actor=current_user.email
    )
    if obra is None:
        raise HTTPException(status_code=404, detail="Obra nao encontrada")
    return ObraRead.model_validate(obra)


@router.delete("/{obra_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_obra_endpoint(
    obra_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await service.delete_obra(db, obra_id, actor=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Obra nao encontrada")


# --- ObraDocumento CRUD ---------------------------------------------------


@router.post(
    "/{obra_id}/documentos",
    response_model=ObraDocumentoRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_obra_documento_endpoint(
    obra_id: int,
    payload: ObraDocumentoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObraDocumentoRead:
    doc = await service.create_obra_documento(
        db, obra_id, payload, actor=current_user.email
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Obra nao encontrada")
    return ObraDocumentoRead.model_validate(doc)


@router.get("/{obra_id}/documentos", response_model=list[ObraDocumentoRead])
async def list_obra_documentos_endpoint(
    obra_id: int, db: AsyncSession = Depends(get_db)
) -> list[ObraDocumentoRead]:
    if (await service.get_obra(db, obra_id)) is None:
        raise HTTPException(status_code=404, detail="Obra nao encontrada")
    rows = await service.list_obra_documentos(db, obra_id)
    return [ObraDocumentoRead.model_validate(r) for r in rows]


@router.put(
    "/documentos/{doc_id}",
    response_model=ObraDocumentoRead,
)
async def update_obra_documento_endpoint(
    doc_id: int,
    payload: ObraDocumentoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObraDocumentoRead:
    doc = await service.update_obra_documento(
        db, doc_id, payload, actor=current_user.email
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
    return ObraDocumentoRead.model_validate(doc)


@router.delete(
    "/documentos/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_obra_documento_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await service.delete_obra_documento(
        db, doc_id, actor=current_user.email
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
