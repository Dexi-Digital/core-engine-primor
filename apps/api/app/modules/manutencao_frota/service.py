"""Modulo B.1 -- Frota / cadastro de veiculos.

Stateless CRUD com auditoria. B.2 (parte diaria + OCR) e B.3 (RPA
Detran) vao consumir esta camada -- por isso `create/update/delete`
ja gravam em `audit_log` desde B.1: quando a RPA Detran comecar a
mexer em `frota_documentos` automaticamente, o historico de quem
mudou o que ja vai estar registrado.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.models import AuditLog
from app.modules.manutencao_frota.models import (
    DocumentoVeiculo,
    Veiculo,
)
from app.modules.manutencao_frota.validators import (
    normalize_chassi,
    normalize_placa,
    normalize_renavam,
)

logger = logging.getLogger(__name__)


# AGENTS.md: "toda mutacao de recurso sensivel grava em audit_log".
# Veiculos rastreiam IPVA + seguros + multas (B.3) -- tudo financeiro,
# precisa virar uma linha em audit_log.
_AUDIT_RESOURCE = "manutencao_frota.veiculo"
_AUDIT_RESOURCE_DOC = "manutencao_frota.documento"
_AUDIT_ACTOR_PLACEHOLDER = "system"


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource: str,
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
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )
    await db.commit()


# --- Veiculo CRUD -----------------------------------------------------------


async def create_veiculo(
    db: AsyncSession,
    *,
    documentos: Sequence[Any] | None = None,
    **fields: Any,
) -> Veiculo:
    # Normalizacao redundante com o schema -- tres motivos: (1) workers
    # / scripts internos podem chamar service direto sem passar pelo
    # schema; (2) garantir invariante "no DB so tem placa normalizada"
    # mesmo sob refactor de schema; (3) DB faz queries por igualdade
    # estrita, nao podemos confiar que a UI vai sempre normalizar.
    if "placa" in fields and fields["placa"]:
        fields["placa"] = normalize_placa(fields["placa"])
    if "renavam" in fields and fields["renavam"]:
        fields["renavam"] = normalize_renavam(fields["renavam"])
    if "chassi" in fields and fields["chassi"]:
        fields["chassi"] = normalize_chassi(fields["chassi"])

    veiculo = Veiculo(**fields)
    if documentos:
        for doc in documentos:
            data = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
            veiculo.documentos.append(DocumentoVeiculo(**data))
    db.add(veiculo)
    await db.commit()
    await db.refresh(veiculo)
    await _record_audit(
        db,
        action="create",
        resource=_AUDIT_RESOURCE,
        resource_id=veiculo.id,
        metadata={
            "placa": veiculo.placa,
            "renavam": veiculo.renavam,
            "marca": veiculo.marca,
            "modelo": veiculo.modelo,
            "obra": veiculo.obra,
        },
    )
    return await get_veiculo(db, veiculo.id) or veiculo


async def get_veiculo(db: AsyncSession, veiculo_id: int) -> Veiculo | None:
    stmt = (
        select(Veiculo)
        .where(Veiculo.id == veiculo_id)
        .options(selectinload(Veiculo.documentos))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_veiculo_by_placa(
    db: AsyncSession, placa: str
) -> Veiculo | None:
    placa_norm = normalize_placa(placa)
    stmt = (
        select(Veiculo)
        .where(Veiculo.placa == placa_norm)
        .options(selectinload(Veiculo.documentos))
        # Se houve troca de placa, mantemos historico (varias rows com
        # mesma placa sao possiveis). UI mostra a mais recente.
        .order_by(Veiculo.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_veiculos(
    db: AsyncSession,
    *,
    status: str | None = None,
    obra: str | None = None,
    tipo: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Veiculo], int]:
    """Lista veiculos com filtros + paginacao."""
    base_filters = []
    if status:
        base_filters.append(Veiculo.status == status)
    if obra:
        base_filters.append(Veiculo.obra == obra)
    if tipo:
        base_filters.append(Veiculo.tipo == tipo)
    if search:
        like = f"%{search.strip()}%"
        # placa/renavam/chassi armazenados sem mascara -- normalizamos
        # a busca pra casar com o que esta no banco.
        placa_search = normalize_placa(search) if search else ""
        renavam_search = normalize_renavam(search) if search else ""
        chassi_search = normalize_chassi(search) if search else ""
        conditions = [
            Veiculo.modelo.ilike(like),
            Veiculo.marca.ilike(like),
        ]
        if placa_search:
            conditions.append(Veiculo.placa.ilike(f"%{placa_search}%"))
        if renavam_search:
            conditions.append(Veiculo.renavam.ilike(f"%{renavam_search}%"))
        if chassi_search:
            conditions.append(Veiculo.chassi.ilike(f"%{chassi_search}%"))
        base_filters.append(or_(*conditions))

    count_stmt = select(func.count(Veiculo.id))
    list_stmt = (
        select(Veiculo)
        .options(selectinload(Veiculo.documentos))
        .order_by(Veiculo.placa)
        .limit(limit)
        .offset(offset)
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = list((await db.execute(list_stmt)).scalars().all())
    return rows, int(total)


async def update_veiculo(
    db: AsyncSession, veiculo_id: int, **fields: Any
) -> Veiculo | None:
    row = await db.get(Veiculo, veiculo_id)
    if row is None:
        return None
    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if hasattr(row, key):
            old = getattr(row, key)
            if old != value:
                changed[key] = {"from": old, "to": value}
            setattr(row, key, value)
    await db.commit()
    if changed:
        await _record_audit(
            db,
            action="update",
            resource=_AUDIT_RESOURCE,
            resource_id=veiculo_id,
            metadata={"changed": changed},
        )
    return await get_veiculo(db, veiculo_id)


async def delete_veiculo(db: AsyncSession, veiculo_id: int) -> bool:
    row = await db.get(Veiculo, veiculo_id)
    if row is None:
        return False
    snapshot = {
        "placa": row.placa,
        "renavam": row.renavam,
        "marca": row.marca,
        "modelo": row.modelo,
    }
    await db.delete(row)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource=_AUDIT_RESOURCE,
        resource_id=veiculo_id,
        metadata=snapshot,
    )
    return True


# --- Documentos -------------------------------------------------------------


async def add_documento(
    db: AsyncSession, veiculo_id: int, **fields: Any
) -> DocumentoVeiculo | None:
    """Adiciona um documento a um veiculo. Retorna None se o veiculo
    nao existir."""
    veiculo = await db.get(Veiculo, veiculo_id)
    if veiculo is None:
        return None
    doc = DocumentoVeiculo(veiculo_id=veiculo_id, **fields)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        action="create",
        resource=_AUDIT_RESOURCE_DOC,
        resource_id=doc.id,
        metadata={
            "veiculo_id": veiculo_id,
            "tipo": doc.tipo,
            "validade": doc.validade,
            "valor": doc.valor,
        },
    )
    return doc


async def update_documento(
    db: AsyncSession, documento_id: int, **fields: Any
) -> DocumentoVeiculo | None:
    row = await db.get(DocumentoVeiculo, documento_id)
    if row is None:
        return None
    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if hasattr(row, key):
            old = getattr(row, key)
            if old != value:
                changed[key] = {"from": old, "to": value}
            setattr(row, key, value)
    await db.commit()
    if changed:
        await _record_audit(
            db,
            action="update",
            resource=_AUDIT_RESOURCE_DOC,
            resource_id=documento_id,
            metadata={"changed": changed, "veiculo_id": row.veiculo_id},
        )
    await db.refresh(row)
    return row


async def delete_documento(
    db: AsyncSession, documento_id: int
) -> bool:
    row = await db.get(DocumentoVeiculo, documento_id)
    if row is None:
        return False
    snapshot = {
        "veiculo_id": row.veiculo_id,
        "tipo": row.tipo,
        "numero": row.numero,
        "validade": row.validade,
    }
    await db.delete(row)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource=_AUDIT_RESOURCE_DOC,
        resource_id=documento_id,
        metadata=snapshot,
    )
    return True


__all__ = [
    "add_documento",
    "create_veiculo",
    "delete_documento",
    "delete_veiculo",
    "get_veiculo",
    "get_veiculo_by_placa",
    "list_veiculos",
    "update_documento",
    "update_veiculo",
]
