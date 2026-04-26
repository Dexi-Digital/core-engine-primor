"""Modulo B (Frota) -- service layer.

Cobre B.1 (CRUD veiculos + documentos) e B.3 (consulta Detran via
Infosimples). Todas as mutacoes gravam em `audit_log` (AGENTS.md).
B.3 alimenta `frota_consultas_detran` (historico) e materializa
multas/IPVA/CRLV em `frota_documentos` com `source=detran_rpa`.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.models import AuditLog
from app.modules.manutencao_frota.models import (
    CONSULTA_ERRO,
    CONSULTA_MOCK,
    CONSULTA_OK,
    DOC_IPVA,
    DOC_LICENCIAMENTO,
    SOURCE_DETRAN_RPA,
    UFS_DETRAN_SUPORTADAS,
    ConsultaDetran,
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
_AUDIT_RESOURCE_CONSULTA = "manutencao_frota.consulta_detran"
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


# --- B.3 -- Consulta Detran (Infosimples) -----------------------------------


def _parse_iso_date(value: Any) -> date | None:
    """Aceita 'YYYY-MM-DD' (Infosimples) ou objeto date. Devolve None
    para qualquer formato nao parseavel -- a UI mostra o documento sem
    validade em vez de levantar (consulta nao deve quebrar pra cliente
    so porque um campo opcional veio mal-formatado)."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def _materializar_documentos(
    db: AsyncSession,
    veiculo_id: int,
    payload: dict[str, Any],
) -> list[int]:
    """Cria rows em `frota_documentos` a partir do payload Infosimples.

    Estrategia: cada consulta gera 0+ documentos (IPVA, licenciamento)
    com `source=detran_rpa`. NAO sobrescreve manuais -- o usuario pode
    ter cadastrado IPVA antes da consulta. A UI mostra historico por
    tipo (rows mais recentes vencem); auditor consegue distinguir
    pela coluna `source`. Retorna a lista de ids criados.

    Multas nao viram documentos individuais (sao N por veiculo e nao
    tem 'validade' no sentido de doc) -- ficam em `payload` da
    consulta para a UI renderizar como tabela.
    """
    novos_ids: list[int] = []
    ipva = payload.get("ipva") or {}
    if ipva and ipva.get("vencimento"):
        venc = _parse_iso_date(ipva.get("vencimento"))
        if venc:
            doc = DocumentoVeiculo(
                veiculo_id=veiculo_id,
                tipo=DOC_IPVA,
                numero=str(ipva.get("exercicio") or ""),
                validade=venc,
                source=SOURCE_DETRAN_RPA,
                observacoes=(
                    "pago"
                    if ipva.get("pago")
                    else "em aberto -- via Infosimples"
                ),
            )
            db.add(doc)
            await db.flush()
            novos_ids.append(doc.id)
    licenciamento = payload.get("licenciamento") or {}
    if licenciamento and licenciamento.get("vencimento"):
        venc = _parse_iso_date(licenciamento.get("vencimento"))
        if venc:
            doc = DocumentoVeiculo(
                veiculo_id=veiculo_id,
                tipo=DOC_LICENCIAMENTO,
                numero=str(licenciamento.get("exercicio") or ""),
                validade=venc,
                source=SOURCE_DETRAN_RPA,
                observacoes=(
                    "pago"
                    if licenciamento.get("pago")
                    else "em aberto -- via Infosimples"
                ),
            )
            db.add(doc)
            await db.flush()
            novos_ids.append(doc.id)
    return novos_ids


async def consultar_detran(
    db: AsyncSession,
    veiculo_id: int,
    uf: str,
    *,
    client: Any,
) -> ConsultaDetran:
    """Consulta Detran via Infosimples e persiste resultado.

    Sempre retorna uma `ConsultaDetran` -- tanto em sucesso (status
    'ok'/'mock') quanto em erro (status 'erro' + `error_msg`). Isso
    permite a UI renderizar o historico de tentativas mesmo quando a
    API esta fora do ar ou o saldo zerou.

    A funcao NAO chama `aclose()` no `client` -- o caller (router via
    singleton, worker via factory) e responsavel pelo lifecycle.
    """
    veiculo = await db.get(Veiculo, veiculo_id)
    if veiculo is None:
        raise ValueError(f"veiculo {veiculo_id} nao encontrado")
    uf_norm = uf.strip().upper()
    if uf_norm not in UFS_DETRAN_SUPORTADAS:
        raise ValueError(
            "uf nao suportada -- "
            + ", ".join(sorted(UFS_DETRAN_SUPORTADAS))
        )

    placa = veiculo.placa  # ja normalizada pelo schema/service
    consulta = ConsultaDetran(
        veiculo_id=veiculo_id,
        placa=placa,
        uf=uf_norm,
        status="pendente",
    )
    db.add(consulta)
    try:
        result = await client.consultar_veiculo(placa, uf_norm)
    except Exception as exc:  # noqa: BLE001 -- guardamos QUALQUER erro
        # Erros de transporte, rate-limit, captcha (futuro), formato
        # inesperado -- todos viram row 'erro'. Nao re-raise: o
        # endpoint devolve 200 com `status='erro'` para a UI poder
        # renderizar o erro inline na lista de consultas.
        logger.warning(
            "consulta_detran %s/%s falhou: %s", placa, uf_norm, exc
        )
        consulta.status = CONSULTA_ERRO
        consulta.error_msg = str(exc)[:500]
        consulta.source = (
            "infosimples_mock"
            if getattr(client, "is_mock", False)
            else "infosimples"
        )
        await db.commit()
        await _record_audit(
            db,
            action="error",
            resource=_AUDIT_RESOURCE_CONSULTA,
            resource_id=consulta.id,
            metadata={
                "veiculo_id": veiculo_id,
                "placa": placa,
                "uf": uf_norm,
                "error": str(exc)[:500],
            },
        )
        await db.refresh(consulta)
        return consulta

    consulta.payload = result
    consulta.source = result.get("source", "infosimples")
    consulta.status = (
        CONSULTA_MOCK
        if consulta.source.endswith("_mock")
        else CONSULTA_OK
    )
    novos_doc_ids = await _materializar_documentos(
        db, veiculo_id, result
    )
    await db.commit()
    await _record_audit(
        db,
        action="create",
        resource=_AUDIT_RESOURCE_CONSULTA,
        resource_id=consulta.id,
        metadata={
            "veiculo_id": veiculo_id,
            "placa": placa,
            "uf": uf_norm,
            "source": consulta.source,
            "n_multas": len(result.get("multas") or []),
            "novos_documentos": novos_doc_ids,
            "situacao": result.get("situacao"),
        },
    )
    await db.refresh(consulta)
    return consulta


async def list_consultas_detran(
    db: AsyncSession,
    *,
    veiculo_id: int | None = None,
    uf: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ConsultaDetran], int]:
    """Lista consultas Detran ordenadas por executed_at desc.

    Sem filtro retorna global; com filtro mostra historico de uma
    placa/UF especifica. UI usa para mostrar "ultimas 10 consultas"
    no detalhe do veiculo.
    """
    base_filters = []
    if veiculo_id is not None:
        base_filters.append(ConsultaDetran.veiculo_id == veiculo_id)
    if uf:
        base_filters.append(ConsultaDetran.uf == uf.strip().upper())

    count_stmt = select(func.count(ConsultaDetran.id))
    list_stmt = (
        select(ConsultaDetran)
        .order_by(ConsultaDetran.executed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = list((await db.execute(list_stmt)).scalars().all())
    return rows, int(total)


# --- Infosimples client factory + singleton --------------------------------


def get_infosimples_client(settings: Any) -> Any:
    """Constroi um novo client real ou mock conforme configuracao.

    Mesma estrategia de DirectData/LLM/OneDrive/Dominio: sem token,
    cai no mock determinístico para nao bloquear dev/CI. Em prod,
    basta configurar `INFOSIMPLES_TOKEN` no env.

    Esta funcao e a "factory" -- cada chamada cria um client novo.
    Para uso na API HTTP use `get_infosimples_singleton()` que cacheia
    para aproveitar o pool TCP do httpx entre requests. Workers
    Celery usam a factory porque rodam em processos separados com
    lifecycle proprio.
    """
    from app.integrations.infosimples.client import InfosimplesClient

    return InfosimplesClient(
        api_token=settings.infosimples_token,
        base_url=settings.infosimples_base_url,
    )


_infosimples_singleton: Any | None = None


def get_infosimples_singleton() -> Any:
    """Devolve um `InfosimplesClient` (ou mock) compartilhado por
    processo. Cacheia para reusar pool de conexoes entre requests --
    consultas Detran sao lentas (20-40s) e o overhead de TCP handshake
    a cada uma e relevante. Em testes, basta limpar via
    `reset_infosimples_singleton()`.
    """
    global _infosimples_singleton
    if _infosimples_singleton is None:
        _infosimples_singleton = get_infosimples_client(_get_settings())
    return _infosimples_singleton


def reset_infosimples_singleton() -> Any | None:
    """Limpa o singleton (lifespan shutdown / testes). Devolve a
    instancia anterior para o caller poder chamar `aclose()`."""
    global _infosimples_singleton
    prev = _infosimples_singleton
    _infosimples_singleton = None
    return prev


def _get_settings() -> Any:
    # Indireto para evitar import circular em tempo de modulo.
    from app.core.config import get_settings

    return get_settings()


__all__ = [
    "add_documento",
    "consultar_detran",
    "create_veiculo",
    "delete_documento",
    "delete_veiculo",
    "get_infosimples_client",
    "get_infosimples_singleton",
    "get_veiculo",
    "get_veiculo_by_placa",
    "list_consultas_detran",
    "list_veiculos",
    "reset_infosimples_singleton",
    "update_documento",
    "update_veiculo",
]
