"""Service + schemas + router para `empresa_documentos` (D1).

Documentos societarios e cadastros oficiais da empresa (contrato
social, alteracoes contratuais, balanco patrimonial, SICAF, CAGEF,
SUCAF). Separado de `certidoes_empresa` que cobre CNDs/atestados com
ciclo curto e alertas 30/15/7/0.

Mutacoes sao sensiveis -- gravam `audit_log` com `actor=current_user.email`.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.licitacoes.models import (
    DOC_EMPRESA_TIPOS_VALIDOS,
    EmpresaDocumento,
)

_AUDIT_RESOURCE = "licitacoes.empresa_documento"
_AUDIT_ACTOR_PLACEHOLDER = _AUDIT_ACTOR_SYSTEM


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )
    await db.commit()


# ----------------------------- schemas ------------------------------------


class EmpresaDocumentoBase(BaseModel):
    empresa_cnpj: str = Field(..., max_length=20)
    tipo: str = Field(..., max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    orgao_emissor: str | None = Field(default=None, max_length=255)
    anexo_path: str | None = Field(default=None, max_length=1024)
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str) -> str:
        if v not in DOC_EMPRESA_TIPOS_VALIDOS:
            raise ValueError(
                "tipo deve ser um de: "
                + ", ".join(sorted(DOC_EMPRESA_TIPOS_VALIDOS))
            )
        return v


class EmpresaDocumentoCreate(EmpresaDocumentoBase):
    pass


class EmpresaDocumentoUpdate(BaseModel):
    tipo: str | None = Field(default=None, max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    orgao_emissor: str | None = Field(default=None, max_length=255)
    anexo_path: str | None = Field(default=None, max_length=1024)
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str | None) -> str | None:
        if v is not None and v not in DOC_EMPRESA_TIPOS_VALIDOS:
            raise ValueError(
                "tipo deve ser um de: "
                + ", ".join(sorted(DOC_EMPRESA_TIPOS_VALIDOS))
            )
        return v


class EmpresaDocumentoRead(EmpresaDocumentoBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    created_at: datetime
    updated_at: datetime


# ----------------------------- service ------------------------------------


async def create_documento(
    db: AsyncSession,
    payload: EmpresaDocumentoCreate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> EmpresaDocumento:
    doc = EmpresaDocumento(**payload.model_dump())
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        action="create",
        resource_id=doc.id,
        metadata={"empresa_cnpj": doc.empresa_cnpj, "tipo": doc.tipo},
        actor=actor,
    )
    return doc


async def list_documentos(
    db: AsyncSession,
    *,
    empresa_cnpj: str | None = None,
    tipo: str | None = None,
    limit: int = 200,
) -> list[EmpresaDocumento]:
    stmt = select(EmpresaDocumento)
    if empresa_cnpj:
        stmt = stmt.where(EmpresaDocumento.empresa_cnpj == empresa_cnpj)
    if tipo:
        stmt = stmt.where(EmpresaDocumento.tipo == tipo)
    stmt = stmt.order_by(EmpresaDocumento.id.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_documento(
    db: AsyncSession, doc_id: int
) -> EmpresaDocumento | None:
    result = await db.execute(
        select(EmpresaDocumento).where(EmpresaDocumento.id == doc_id)
    )
    return result.scalars().first()


async def update_documento(
    db: AsyncSession,
    doc_id: int,
    payload: EmpresaDocumentoUpdate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> EmpresaDocumento | None:
    doc = await get_documento(db, doc_id)
    if doc is None:
        return None
    changes: dict[str, Any] = {}
    for field, value in payload.model_dump(exclude_unset=True).items():
        if getattr(doc, field) != value:
            changes[field] = value
            setattr(doc, field, value)
    if changes:
        await db.commit()
        await db.refresh(doc)
        await _record_audit(
            db,
            action="update",
            resource_id=doc.id,
            metadata={"changes": list(changes.keys())},
            actor=actor,
        )
    return doc


async def delete_documento(
    db: AsyncSession,
    doc_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    doc = await get_documento(db, doc_id)
    if doc is None:
        return False
    cnpj = doc.empresa_cnpj
    tipo = doc.tipo
    await db.delete(doc)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource_id=doc_id,
        metadata={"empresa_cnpj": cnpj, "tipo": tipo},
        actor=actor,
    )
    return True


# ----------------------------- router -------------------------------------


router = APIRouter(prefix="/api/v1/empresa-documentos", tags=["empresa-documentos"])


@router.post(
    "", response_model=EmpresaDocumentoRead, status_code=status.HTTP_201_CREATED
)
async def create_endpoint(
    payload: EmpresaDocumentoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmpresaDocumentoRead:
    doc = await create_documento(db, payload, actor=current_user.email)
    return EmpresaDocumentoRead.model_validate(doc)


@router.get("", response_model=list[EmpresaDocumentoRead])
async def list_endpoint(
    db: AsyncSession = Depends(get_db),
    empresa_cnpj: str | None = None,
    tipo: str | None = None,
    limit: int = 200,
) -> list[EmpresaDocumentoRead]:
    rows = await list_documentos(
        db, empresa_cnpj=empresa_cnpj, tipo=tipo, limit=limit
    )
    return [EmpresaDocumentoRead.model_validate(r) for r in rows]


@router.get("/{doc_id}", response_model=EmpresaDocumentoRead)
async def get_endpoint(
    doc_id: int, db: AsyncSession = Depends(get_db)
) -> EmpresaDocumentoRead:
    doc = await get_documento(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
    return EmpresaDocumentoRead.model_validate(doc)


@router.put("/{doc_id}", response_model=EmpresaDocumentoRead)
async def update_endpoint(
    doc_id: int,
    payload: EmpresaDocumentoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmpresaDocumentoRead:
    doc = await update_documento(
        db, doc_id, payload, actor=current_user.email
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
    return EmpresaDocumentoRead.model_validate(doc)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_documento(db, doc_id, actor=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
