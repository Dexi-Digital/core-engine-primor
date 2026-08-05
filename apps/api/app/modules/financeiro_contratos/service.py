"""Service do ciclo de contratos (Squad 5, demanda #12).

Mesmo desenho do D.6 (licitacoes/certidoes.py): funcoes async puras
sobre AsyncSession, audit_log em toda mutacao, helpers de status
computado reaproveitados do proprio D.6 (a semantica "data limite +
janelas 30/15/7/0" e identica -- mudou so o substantivo).
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO_VALIDOS,
    TIPOS_CONTRATO_VALIDOS,
    Contrato,
)
from app.modules.licitacoes.certidoes import compute_status
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "financeiro.contrato"


# Reuso deliberado: as regras de bucket (vigente/vencendo/vencido/
# sem_validade, threshold 30d) sao as mesmas das certidoes. Para
# contratos, `sem_validade` significa prazo indeterminado (UI rotula
# "Sem prazo").
compute_vencimento_status = compute_status


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
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


async def list_contratos(
    db: AsyncSession,
    *,
    status: str | None = None,
    tipo: str | None = None,
    obra_id: int | None = None,
    vence_em_dias: int | None = None,
    today: _date | None = None,
) -> list[Contrato]:
    stmt = select(Contrato).order_by(Contrato.data_fim.asc().nullslast())
    if status:
        stmt = stmt.where(Contrato.status == status)
    if tipo:
        stmt = stmt.where(Contrato.tipo == tipo)
    if obra_id is not None:
        stmt = stmt.where(Contrato.obra_id == obra_id)
    if vence_em_dias is not None:
        today = today or _date.today()
        stmt = stmt.where(
            Contrato.data_fim.isnot(None),
            Contrato.data_fim >= today,
            Contrato.data_fim <= today + timedelta(days=vence_em_dias),
        )
    return list((await db.execute(stmt)).scalars().all())


async def get_contrato(db: AsyncSession, contrato_id: int) -> Contrato | None:
    return await db.get(Contrato, contrato_id)


async def create_contrato(
    db: AsyncSession,
    *,
    titulo: str,
    contraparte_nome: str,
    tipo: str,
    data_inicio: _date,
    contraparte_documento: str | None = None,
    obra_id: int | None = None,
    valor: Decimal | int | str | None = None,
    data_fim: _date | None = None,
    status: str = "rascunho",
    easyjur_ref: str | None = None,
    observacoes: str | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> Contrato:
    # Diferente das certidoes (string livre tolerada), tipo/status de
    # contrato sao um enum fechado -- valor fora da lista e erro de
    # programacao ou payload malicioso, nao um "custom" legitimo.
    if tipo not in TIPOS_CONTRATO_VALIDOS:
        raise ValueError(f"tipo de contrato invalido: {tipo}")
    if status not in STATUS_CONTRATO_VALIDOS:
        raise ValueError(f"status de contrato invalido: {status}")
    row = Contrato(
        titulo=titulo,
        contraparte_nome=contraparte_nome,
        contraparte_documento=contraparte_documento,
        tipo=tipo,
        obra_id=obra_id,
        valor=Decimal(str(valor)) if valor is not None else None,
        data_inicio=data_inicio,
        data_fim=data_fim,
        status=status,
        easyjur_ref=easyjur_ref,
        observacoes=observacoes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _record_audit(
        db,
        action="create",
        resource_id=row.id,
        actor=actor,
        metadata={
            "titulo": row.titulo,
            "tipo": row.tipo,
            "contraparte_nome": row.contraparte_nome,
            "data_fim": row.data_fim,
        },
    )
    return row


async def update_contrato(
    db: AsyncSession,
    contrato_id: int,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
    **fields: object,
) -> Contrato | None:
    row = await db.get(Contrato, contrato_id)
    if row is None:
        return None
    if "tipo" in fields and fields["tipo"] not in TIPOS_CONTRATO_VALIDOS:
        raise ValueError(f"tipo de contrato invalido: {fields['tipo']}")
    if "status" in fields and fields["status"] not in STATUS_CONTRATO_VALIDOS:
        raise ValueError(f"status de contrato invalido: {fields['status']}")
    if "valor" in fields and fields["valor"] is not None:
        fields["valor"] = Decimal(str(fields["valor"]))
    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if hasattr(row, key):
            old = getattr(row, key)
            if old != value:
                changed[key] = {"from": old, "to": value}
            setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    if changed:
        await _record_audit(
            db,
            action="update",
            resource_id=row.id,
            actor=actor,
            metadata={"changed": changed},
        )
    return row


async def delete_contrato(
    db: AsyncSession,
    contrato_id: int,
    *,
    storage: EditaisStorage | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> bool:
    """Remove o contrato e, se `storage` foi passado, o PDF anexo.

    Best-effort no storage (mesmo racional do delete_certidao): a linha
    do DB ja foi removida; falha na limpeza do arquivo apenas loga.
    """
    row = await db.get(Contrato, contrato_id)
    if row is None:
        return False
    snapshot = {
        "titulo": row.titulo,
        "tipo": row.tipo,
        "contraparte_nome": row.contraparte_nome,
    }
    arquivo_path = row.arquivo_path
    await db.delete(row)
    await db.commit()
    if storage is not None and arquivo_path:
        try:
            await storage.delete(arquivo_path)
        except Exception:  # noqa: BLE001
            logger.warning(
                "falha ao remover PDF do contrato em %s (DB ja apagou)",
                arquivo_path,
            )
    await _record_audit(
        db, action="delete", resource_id=contrato_id, actor=actor, metadata=snapshot
    )
    return True
