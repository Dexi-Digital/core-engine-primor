"""Service layer para Obras (D1).

CRUD com `audit_log` + `actor` threading -- mesmo padrao dos modulos
sensiveis (PR #21). Mutacoes de obra/documento sao consideradas
sensiveis porque alimentam a base do diagnostico documental.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.modules.obras.models import (
    Obra,
    ObraDocumento,
)
from app.modules.obras.schemas import (
    ObraCreate,
    ObraDocumentoCreate,
    ObraDocumentoUpdate,
    ObraUpdate,
)

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE_OBRA = "obras.obra"
_AUDIT_RESOURCE_DOC = "obras.documento"
_AUDIT_ACTOR_PLACEHOLDER = "system"


async def _record_audit(
    db: AsyncSession,
    *,
    resource: str,
    action: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str)
            if metadata
            else None,
        )
    )
    await db.commit()


# ----------------------- Obra CRUD ----------------------------------------


async def create_obra(
    db: AsyncSession,
    payload: ObraCreate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> Obra:
    obra = Obra(**payload.model_dump())
    db.add(obra)
    await db.commit()
    await db.refresh(obra)
    await _record_audit(
        db,
        resource=_AUDIT_RESOURCE_OBRA,
        action="create",
        resource_id=obra.id,
        metadata={"codigo": obra.codigo, "nome": obra.nome},
        actor=actor,
    )
    return obra


async def list_obras(
    db: AsyncSession,
    *,
    status: str | None = None,
    uf: str | None = None,
    limit: int = 200,
) -> list[Obra]:
    stmt = select(Obra)
    if status:
        stmt = stmt.where(Obra.status == status)
    if uf:
        stmt = stmt.where(Obra.uf == uf)
    stmt = stmt.order_by(Obra.codigo).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_obra(db: AsyncSession, obra_id: int) -> Obra | None:
    result = await db.execute(select(Obra).where(Obra.id == obra_id))
    return result.scalars().first()


async def update_obra(
    db: AsyncSession,
    obra_id: int,
    payload: ObraUpdate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> Obra | None:
    obra = await get_obra(db, obra_id)
    if obra is None:
        return None
    changes: dict[str, Any] = {}
    for field, value in payload.model_dump(exclude_unset=True).items():
        if getattr(obra, field) != value:
            changes[field] = value
            setattr(obra, field, value)
    if changes:
        await db.commit()
        await db.refresh(obra)
        await _record_audit(
            db,
            resource=_AUDIT_RESOURCE_OBRA,
            action="update",
            resource_id=obra.id,
            metadata={"changes": list(changes.keys())},
            actor=actor,
        )
    return obra


async def delete_obra(
    db: AsyncSession,
    obra_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    obra = await get_obra(db, obra_id)
    if obra is None:
        return False
    codigo = obra.codigo
    await db.delete(obra)
    await db.commit()
    await _record_audit(
        db,
        resource=_AUDIT_RESOURCE_OBRA,
        action="delete",
        resource_id=obra_id,
        metadata={"codigo": codigo},
        actor=actor,
    )
    return True


# ----------------------- ObraDocumento CRUD -------------------------------


async def create_obra_documento(
    db: AsyncSession,
    obra_id: int,
    payload: ObraDocumentoCreate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> ObraDocumento | None:
    if (await get_obra(db, obra_id)) is None:
        return None
    doc = ObraDocumento(obra_id=obra_id, **payload.model_dump())
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        resource=_AUDIT_RESOURCE_DOC,
        action="create",
        resource_id=doc.id,
        metadata={"obra_id": obra_id, "tipo": doc.tipo},
        actor=actor,
    )
    return doc


async def list_obra_documentos(
    db: AsyncSession, obra_id: int
) -> list[ObraDocumento]:
    stmt = (
        select(ObraDocumento)
        .where(ObraDocumento.obra_id == obra_id)
        .order_by(ObraDocumento.tipo, ObraDocumento.id.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_obra_documento(
    db: AsyncSession, doc_id: int
) -> ObraDocumento | None:
    result = await db.execute(
        select(ObraDocumento).where(ObraDocumento.id == doc_id)
    )
    return result.scalars().first()


async def update_obra_documento(
    db: AsyncSession,
    doc_id: int,
    payload: ObraDocumentoUpdate,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> ObraDocumento | None:
    doc = await get_obra_documento(db, doc_id)
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
            resource=_AUDIT_RESOURCE_DOC,
            action="update",
            resource_id=doc.id,
            metadata={"obra_id": doc.obra_id, "changes": list(changes.keys())},
            actor=actor,
        )
    return doc


async def delete_obra_documento(
    db: AsyncSession,
    doc_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    doc = await get_obra_documento(db, doc_id)
    if doc is None:
        return False
    obra_id = doc.obra_id
    tipo = doc.tipo
    await db.delete(doc)
    await db.commit()
    await _record_audit(
        db,
        resource=_AUDIT_RESOURCE_DOC,
        action="delete",
        resource_id=doc_id,
        metadata={"obra_id": obra_id, "tipo": tipo},
        actor=actor,
    )
    return True
