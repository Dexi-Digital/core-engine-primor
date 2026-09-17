"""Comparacao de versao entre os servicos (API x worker).

O worker registra a impressao digital do codigo dele no `audit_log` a
cada boot; a API compara com a propria. Divergencia significa que um
dos dois nao foi redeployado depois de uma alteracao em `apps/api` --
situacao que nao produz erro nenhum, so comportamento diferente entre
os dois (ver `app.core.version`).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.version import code_fingerprint

ACAO_BOOT_WORKER = "worker_boot"
RECURSO_VERSAO = "observability.versao"


async def registrar_boot_worker(
    db: AsyncSession, *, fingerprint: str, actor: str = "system:worker"
) -> None:
    """Chamado pelo worker no boot.

    Uma row por boot -- o historico mostra quando cada versao entrou no
    ar, o que ajuda a datar uma divergencia depois do fato.
    """
    db.add(
        AuditLog(
            actor=actor,
            action=ACAO_BOOT_WORKER,
            resource=RECURSO_VERSAO,
            resource_id=None,
            metadata_json=json.dumps({"fingerprint": fingerprint}),
        )
    )
    await db.commit()


async def comparar_versoes(db: AsyncSession) -> dict[str, Any]:
    """Versao da API (esta instancia) x ultimo boot do worker.

    `em_sincronia` e None quando o worker nunca registrou boot -- que e
    diferente de estar divergente. Nao afirmar e melhor do que afirmar
    errado: worker que nunca subiu nao esta "em dia".

    Metadata corrompida tambem cai em None em vez de estourar: isto e
    telemetria de diagnostico, e quebrar a consulta por causa de uma
    row ruim seria pior do que informar "desconhecido".
    """
    api_fp = code_fingerprint()
    res = await db.execute(
        select(AuditLog)
        .where(AuditLog.action == ACAO_BOOT_WORKER)
        .where(AuditLog.resource == RECURSO_VERSAO)
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    row = res.scalar_one_or_none()

    worker_fp: str | None = None
    worker_em: datetime | None = None
    if row is not None:
        try:
            dados = json.loads(row.metadata_json or "{}")
            worker_fp = dados.get("fingerprint") if isinstance(dados, dict) else None
        except (ValueError, TypeError):
            worker_fp = None
        worker_em = getattr(row, "created_at", None)

    return {
        "api_fingerprint": api_fp,
        "worker_fingerprint": worker_fp,
        "worker_boot_em": worker_em,
        "em_sincronia": (api_fp == worker_fp) if worker_fp else None,
    }
