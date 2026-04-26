"""Service + schemas + router para `dp_employee_documents` (D1).

Documentos genericos vinculados a um funcionario (NR-10/12/18/35,
exame toxicologico, ordem de servico, lista de integracao, ficha de
EPI, termo LGPD, contrato de experiencia, acordo de compensacao de
horas, RCT, PPP, etc.). ASO continua nas colunas `aso_*` em `Employee`.

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

from app.audit.models import AuditLog
from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.dp_sesmt.models import (
    DOC_EMP_TIPOS_VALIDOS,
    Employee,
    EmployeeDocument,
)

_AUDIT_RESOURCE = "dp_sesmt.employee_document"
_AUDIT_ACTOR_PLACEHOLDER = "system"


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


class EmployeeDocumentBase(BaseModel):
    tipo: str = Field(..., max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    anexo_path: str | None = Field(default=None, max_length=1024)
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str) -> str:
        if v not in DOC_EMP_TIPOS_VALIDOS:
            raise ValueError(
                "tipo deve ser um de: "
                + ", ".join(sorted(DOC_EMP_TIPOS_VALIDOS))
            )
        return v


class EmployeeDocumentCreate(EmployeeDocumentBase):
    pass


class EmployeeDocumentUpdate(BaseModel):
    tipo: str | None = Field(default=None, max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    anexo_path: str | None = Field(default=None, max_length=1024)
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str | None) -> str | None:
        if v is not None and v not in DOC_EMP_TIPOS_VALIDOS:
            raise ValueError(
                "tipo deve ser um de: "
                + ", ".join(sorted(DOC_EMP_TIPOS_VALIDOS))
            )
        return v


class EmployeeDocumentRead(EmployeeDocumentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employee_id: int
    source: str
    created_at: datetime
    updated_at: datetime


# ----------------------------- service ------------------------------------


async def _get_employee(db: AsyncSession, employee_id: int) -> Employee | None:
    res = await db.execute(select(Employee).where(Employee.id == employee_id))
    return res.scalars().first()


async def create_documento(
    db: AsyncSession,
    employee_id: int,
    payload: EmployeeDocumentCreate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> EmployeeDocument | None:
    if (await _get_employee(db, employee_id)) is None:
        return None
    doc = EmployeeDocument(employee_id=employee_id, **payload.model_dump())
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        action="create",
        resource_id=doc.id,
        metadata={"employee_id": employee_id, "tipo": doc.tipo},
        actor=actor,
    )
    return doc


async def list_documentos(
    db: AsyncSession,
    employee_id: int,
) -> list[EmployeeDocument]:
    stmt = (
        select(EmployeeDocument)
        .where(EmployeeDocument.employee_id == employee_id)
        .order_by(EmployeeDocument.tipo, EmployeeDocument.id.desc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def get_documento(
    db: AsyncSession, doc_id: int
) -> EmployeeDocument | None:
    res = await db.execute(
        select(EmployeeDocument).where(EmployeeDocument.id == doc_id)
    )
    return res.scalars().first()


async def update_documento(
    db: AsyncSession,
    doc_id: int,
    payload: EmployeeDocumentUpdate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> EmployeeDocument | None:
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
            metadata={
                "employee_id": doc.employee_id,
                "changes": list(changes.keys()),
            },
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
    employee_id = doc.employee_id
    tipo = doc.tipo
    await db.delete(doc)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource_id=doc_id,
        metadata={"employee_id": employee_id, "tipo": tipo},
        actor=actor,
    )
    return True


# ----------------------------- router -------------------------------------


router = APIRouter(
    prefix="/api/v1/dp-sesmt/employees",
    tags=["dp-sesmt-employee-docs"],
)


@router.post(
    "/{employee_id}/documentos",
    response_model=EmployeeDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_endpoint(
    employee_id: int,
    payload: EmployeeDocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmployeeDocumentRead:
    doc = await create_documento(
        db, employee_id, payload, actor=current_user.email
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Funcionario nao encontrado")
    return EmployeeDocumentRead.model_validate(doc)


@router.get(
    "/{employee_id}/documentos",
    response_model=list[EmployeeDocumentRead],
)
async def list_endpoint(
    employee_id: int, db: AsyncSession = Depends(get_db)
) -> list[EmployeeDocumentRead]:
    if (await _get_employee(db, employee_id)) is None:
        raise HTTPException(status_code=404, detail="Funcionario nao encontrado")
    rows = await list_documentos(db, employee_id)
    return [EmployeeDocumentRead.model_validate(r) for r in rows]


@router.put(
    "/documentos/{doc_id}",
    response_model=EmployeeDocumentRead,
)
async def update_endpoint(
    doc_id: int,
    payload: EmployeeDocumentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmployeeDocumentRead:
    doc = await update_documento(
        db, doc_id, payload, actor=current_user.email
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
    return EmployeeDocumentRead.model_validate(doc)


@router.delete(
    "/documentos/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_documento(db, doc_id, actor=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
