"""Modulo B (Frota) -- service layer.

Cobre B.1 (CRUD veiculos + documentos) e B.3 (consulta Detran via
Infosimples). Todas as mutacoes gravam em `audit_log` (AGENTS.md).
B.3 alimenta `frota_consultas_detran` (historico) e materializa
multas/IPVA/CRLV em `frota_documentos` com `source=detran_rpa`.
"""
from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.models import AuditLog
from app.modules.manutencao_frota.models import (
    CONSULTA_ERRO,
    CONSULTA_MOCK,
    CONSULTA_OK,
    DOC_IPVA,
    DOC_LICENCIAMENTO,
    PARTE_ERRO,
    PARTE_PENDENTE,
    PARTE_PROCESSADO,
    PARTE_REVISADO,
    SOURCE_DETRAN_RPA,
    UFS_DETRAN_SUPORTADAS,
    ConsultaDetran,
    DocumentoVeiculo,
    ParteDiaria,
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
_AUDIT_RESOURCE_PARTE = "manutencao_frota.parte_diaria"
# Default usado em paths sem usuario logado (worker OCR, worker Detran,
# falhas de dispatch sincrono que viram erro depois). Mutacoes vindas
# de requests HTTP devem passar `actor=current_user.email` -- ver router.
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
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
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
        actor=actor,
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
    db: AsyncSession,
    veiculo_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
    **fields: Any,
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
            actor=actor,
            metadata={"changed": changed},
        )
    return await get_veiculo(db, veiculo_id)


async def delete_veiculo(
    db: AsyncSession,
    veiculo_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    row = await db.get(Veiculo, veiculo_id)
    if row is None:
        return False
    snapshot = {
        "placa": row.placa,
        "renavam": row.renavam,
        "marca": row.marca,
        "modelo": row.modelo,
    }
    # Parte diaria sobrevive ao delete do veiculo (auditoria operacional);
    # ON DELETE SET NULL no DB cuida disso em Postgres, mas SQLite (testes)
    # nao enforca FKs por default. Setamos manualmente para garantir o
    # mesmo comportamento entre os dois backends.
    from sqlalchemy import update as sql_update

    await db.execute(
        sql_update(ParteDiaria)
        .where(ParteDiaria.veiculo_id == veiculo_id)
        .values(veiculo_id=None)
    )
    await db.delete(row)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource=_AUDIT_RESOURCE,
        resource_id=veiculo_id,
        actor=actor,
        metadata=snapshot,
    )
    return True


# --- Documentos -------------------------------------------------------------


async def add_documento(
    db: AsyncSession,
    veiculo_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
    **fields: Any,
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
        actor=actor,
        metadata={
            "veiculo_id": veiculo_id,
            "tipo": doc.tipo,
            "validade": doc.validade,
            "valor": doc.valor,
        },
    )
    return doc


async def update_documento(
    db: AsyncSession,
    documento_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
    **fields: Any,
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
            actor=actor,
            metadata={"changed": changed, "veiculo_id": row.veiculo_id},
        )
    await db.refresh(row)
    return row


async def delete_documento(
    db: AsyncSession,
    documento_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
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
        actor=actor,
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
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
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
            actor=actor,
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
        actor=actor,
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


# --- B.2 -- Parte Diaria (OCR via Document AI) -----------------------------


def _parse_iso_date_str(v: Any) -> date | None:
    """Aceita string ISO (`YYYY-MM-DD` ou `YYYY-MM-DDTHH:MM:SS`) ou
    objeto `date` ja parseado. Devolve `None` se nao reconhecer."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except (TypeError, ValueError):
            return None
    return None


def _coerce_horimetro(v: Any) -> Any:
    """Document AI devolve numeros como float; SQLAlchemy Numeric
    aceita Decimal/float/str. Mantemos float para evitar conversao
    explicita aqui (Pydantic na resposta normaliza via Decimal)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def _stream_bytes(payload: bytes):
    """Adapta `bytes` para o contrato AsyncIterator de
    `EditaisStorage.save()` (mesma helper do modulo fiscal)."""
    yield payload


async def create_parte_diaria(
    db: AsyncSession,
    *,
    content: bytes,
    filename: str,
    mime_type: str,
    storage: Any,
    storage_subdir_bucket: int = 0,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> ParteDiaria:
    """Cria parte diaria com anexo + status `pendente`.

    O OCR roda async no worker (`apps/workers/.../manutencao.py`).
    Endpoint chama esta funcao para registrar o upload e dispara a
    task. A view de detalhe mostra `pendente` enquanto worker processa.
    """
    parte = ParteDiaria(
        filename_original=filename[:512],
        mime_type=mime_type[:64],
        ocr_status=PARTE_PENDENTE,
        ocr_source="google_documentai_mock",  # ajustado pos-OCR
    )
    db.add(parte)
    await db.flush()  # precisamos do id para chave de storage

    storage_path, _size = await storage.save(
        # `licitacao_id` e o nome legado do `EditaisStorage.save()`;
        # aqui usamos o id da parte diaria como bucket logico para
        # nao misturar com fiscal/licitacoes.
        licitacao_id=parte.id,
        filename=filename,
        content=_stream_bytes(content),
    )
    parte.anexo_path = storage_path
    await db.commit()
    await _record_audit(
        db,
        action="create",
        resource=_AUDIT_RESOURCE_PARTE,
        resource_id=parte.id,
        actor=actor,
        metadata={
            "filename": filename,
            "mime_type": mime_type,
            "size": len(content),
        },
    )
    await db.refresh(parte)
    return parte


async def find_parte_diaria_by_client_uuid(
    db: AsyncSession, client_uuid: str
) -> ParteDiaria | None:
    """Lookup por client_uuid para idempotencia de envio offline (PWA).

    Quando o PWA reenvia uma parte que estava na fila local (depois
    de cair a conexao), o backend devolve a row ja criada em vez de
    duplicar. Unique parcial em PG garante invariante a nivel de
    banco; este lookup serve de fast-path para devolver a row sem
    levantar IntegrityError.
    """
    res = await db.execute(
        select(ParteDiaria).where(ParteDiaria.client_uuid == client_uuid)
    )
    return res.scalar_one_or_none()


async def create_parte_diaria_manual(
    db: AsyncSession,
    *,
    payload: dict[str, Any],
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> tuple[ParteDiaria, bool]:
    """Cria parte diaria a partir de apontamento manual (PWA mobile, D5).

    Sem anexo, sem OCR -- os campos vem direto do form do PWA. Row
    nasce com `ocr_status='revisado'` (ja conferida pelo apontador
    em campo) e `ocr_source='manual_pwa'`.

    Idempotencia via `client_uuid`: se o cliente reenviar a mesma
    parte (retry pos-reconexao), devolve `(row_existente, False)` em
    vez de criar duplicata.

    Retorna `(parte, criada_agora)` -- caller usa o flag pra decidir
    o status code (201 vs 200).
    """
    client_uuid = payload.get("client_uuid")
    if client_uuid:
        existing = await find_parte_diaria_by_client_uuid(db, client_uuid)
        if existing is not None:
            return existing, False

    # Sanitiza/valida horimetro/km: fim >= inicio (campos do model
    # sao Numeric/Integer; o caller ja validou com Pydantic, aqui
    # so cruzamos os pares). Aceitamos null em qualquer um -- o
    # apontador pode salvar parcialmente sem sinal e completar dps.
    # Levantamos ValueError seguindo a convencao do resto do service
    # (consultar_detran, processar_ocr); router converte em 422.
    h_ini, h_fim = payload.get("horimetro_inicio"), payload.get("horimetro_fim")
    if h_ini is not None and h_fim is not None and h_fim < h_ini:
        raise ValueError("horimetro_fim deve ser >= horimetro_inicio")
    k_ini, k_fim = payload.get("km_inicio"), payload.get("km_fim")
    if k_ini is not None and k_fim is not None and k_fim < k_ini:
        raise ValueError("km_fim deve ser >= km_inicio")

    # Valida FK do veiculo ANTES do INSERT. Se um PWA com cache
    # stale enviar veiculo_id que ja foi removido, queremos um
    # ValueError -> 422 explicando o problema, em vez de cair no
    # try/except IntegrityError abaixo (que e exclusivo do race do
    # client_uuid) e voltar 500. Mesmo padrao de `add_documento`.
    veiculo_id = payload.get("veiculo_id")
    if veiculo_id is not None:
        veiculo = await db.get(Veiculo, veiculo_id)
        if veiculo is None:
            raise ValueError(f"veiculo_id {veiculo_id} nao existe")

    parte = ParteDiaria(
        data=payload.get("data"),
        veiculo_id=payload.get("veiculo_id"),
        operador=payload.get("operador"),
        obra=payload.get("obra"),
        equipamento=payload.get("equipamento"),
        placa=payload.get("placa"),
        horimetro_inicio=h_ini,
        horimetro_fim=h_fim,
        km_inicio=k_ini,
        km_fim=k_fim,
        combustivel_litros=payload.get("combustivel_litros"),
        combustivel_custo=payload.get("combustivel_custo"),
        observacoes=payload.get("observacoes"),
        client_uuid=client_uuid,
        ocr_status=PARTE_REVISADO,  # entrada manual ja e dada como conferida
        ocr_source="manual_pwa",
    )
    db.add(parte)
    try:
        await db.commit()
    except IntegrityError:
        # TOCTOU: requisicao concorrente (ex.: duas tabs do PWA
        # drenando a mesma fila simultaneamente) ja gravou uma
        # parte com este client_uuid entre o fast-path acima e
        # o commit. Indice unique parcial em PG explode aqui.
        # Tratamos como idempotente: rollback, busca a row que
        # ganhou o race, devolve como `criada_agora=False`.
        await db.rollback()
        if client_uuid:
            existing = await find_parte_diaria_by_client_uuid(db, client_uuid)
            if existing is not None:
                return existing, False
        raise
    await db.refresh(parte)
    await _record_audit(
        db,
        action="create_manual",
        resource=_AUDIT_RESOURCE_PARTE,
        resource_id=parte.id,
        actor=actor,
        metadata={
            "veiculo_id": parte.veiculo_id,
            "obra": parte.obra,
            "data": parte.data.isoformat() if parte.data else None,
            "client_uuid": client_uuid,
            "source": "manual_pwa",
        },
    )
    return parte, True


# Janela do gatilho de manutencao preventiva. 250h e padrao de OEM
# (CAT/Komatsu/Volvo) para troca de oleo motor de equipamento pesado.
MANUTENCAO_PREVENTIVA_HORAS_INTERVALO = Decimal("250")


def calcular_consumo_parte_diaria(
    parte: ParteDiaria,
    *,
    horimetro_anterior: Decimal | None = None,
) -> dict[str, Any]:
    """Deriva metricas de consumo a partir dos campos brutos da parte.

    Nao mexe no banco -- so calcula. Caller decide se exibe na UI,
    grava em outro lugar ou dispara alerta. Cada metrica e
    independente: ausencia de combustivel zera so o consumo, nao
    invalida horas_trabalhadas.

    `horimetro_anterior` e o `horimetro_fim` do ultimo apontamento
    do mesmo veiculo -- usado para detectar travessia de multiplo de
    250h (gatilho de manutencao preventiva). Se None, nao dispara
    alerta (primeira parte ou veiculo sem historico).
    """
    horas: Decimal | None = None
    km_rodados: int | None = None
    cons_lh: Decimal | None = None
    cons_kml: Decimal | None = None
    custo_h: Decimal | None = None
    alerta = False

    if parte.horimetro_inicio is not None and parte.horimetro_fim is not None:
        horas = parte.horimetro_fim - parte.horimetro_inicio
        if horas <= 0:
            horas = None  # nao calculamos consumo de jornada com 0h

    if parte.km_inicio is not None and parte.km_fim is not None:
        diff = parte.km_fim - parte.km_inicio
        if diff > 0:
            km_rodados = diff

    if (
        horas is not None
        and parte.combustivel_litros is not None
        and parte.combustivel_litros > 0
    ):
        cons_lh = (parte.combustivel_litros / horas).quantize(Decimal("0.001"))

    if (
        km_rodados is not None
        and parte.combustivel_litros is not None
        and parte.combustivel_litros > 0
    ):
        cons_kml = (
            Decimal(km_rodados) / parte.combustivel_litros
        ).quantize(Decimal("0.001"))

    if (
        horas is not None
        and parte.combustivel_custo is not None
        and parte.combustivel_custo >= 0
    ):
        custo_h = (parte.combustivel_custo / horas).quantize(Decimal("0.01"))

    if (
        horimetro_anterior is not None
        and parte.horimetro_fim is not None
        and parte.horimetro_fim > horimetro_anterior
    ):
        # Cruzou um multiplo de 250h desde o ultimo apontamento?
        marco_anterior = (
            horimetro_anterior // MANUTENCAO_PREVENTIVA_HORAS_INTERVALO
        )
        marco_atual = parte.horimetro_fim // MANUTENCAO_PREVENTIVA_HORAS_INTERVALO
        if marco_atual > marco_anterior:
            alerta = True

    return {
        "parte_diaria_id": parte.id,
        "horas_trabalhadas": horas,
        "km_rodados": km_rodados,
        "consumo_litros_por_hora": cons_lh,
        "consumo_km_por_litro": cons_kml,
        "custo_por_hora": custo_h,
        "alerta_manutencao_preventiva": alerta,
    }


async def get_consumo_parte_diaria(
    db: AsyncSession, parte_id: int
) -> dict[str, Any] | None:
    """Carrega a parte e o ultimo horimetro do mesmo veiculo, devolve consumo."""
    parte = await get_parte_diaria(db, parte_id)
    if parte is None:
        return None

    horim_anterior: Decimal | None = None
    if parte.veiculo_id is not None and parte.data is not None:
        # Ultimo horimetro_fim antes desta data, para o mesmo veiculo.
        # Trabalhamos so com partes ja revisadas (ocr_status='revisado'
        # ou 'processado') -- pendentes/erro nao contam para gatilho.
        res = await db.execute(
            select(ParteDiaria.horimetro_fim)
            .where(ParteDiaria.veiculo_id == parte.veiculo_id)
            .where(ParteDiaria.id != parte.id)
            .where(ParteDiaria.horimetro_fim.is_not(None))
            .where(ParteDiaria.data.is_not(None))
            .where(ParteDiaria.data < parte.data)
            # Pendente/erro tem horimetro_fim cru de OCR ainda nao
            # validado -- usar isso como base do gatilho de 250h
            # geraria alerta espurio (ou perderia um real). So
            # contam revisado/processado.
            .where(ParteDiaria.ocr_status.in_([PARTE_REVISADO, PARTE_PROCESSADO]))
            # Tiebreaker por id quando ha varias partes na mesma
            # data (ex.: turno manha + tarde). Sem isso, o DB
            # poderia escolher qualquer uma e o gatilho de 250h
            # ficaria nao-deterministico.
            .order_by(ParteDiaria.data.desc(), ParteDiaria.id.desc())
            .limit(1)
        )
        row = res.scalar_one_or_none()
        if row is not None:
            horim_anterior = row

    return calcular_consumo_parte_diaria(parte, horimetro_anterior=horim_anterior)


async def mark_parte_diaria_erro(
    db: AsyncSession,
    parte_id: int,
    error_msg: str,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> ParteDiaria | None:
    """Marca uma parte_diaria como `erro` com mensagem truncada.

    Usado quando o dispatch para o worker falha (broker down) -- a row
    ja existe com `pendente`, mas o caller precisa sinalizar para a UI
    que o pipeline nao vai rodar sem intervencao. Audita `error` em
    audit_log para rastreio.
    """
    res = await db.execute(select(ParteDiaria).where(ParteDiaria.id == parte_id))
    parte = res.scalar_one_or_none()
    if parte is None:
        return None
    parte.ocr_status = PARTE_ERRO
    parte.ocr_error_msg = (error_msg or "")[:500]
    await db.commit()
    await db.refresh(parte)
    await _record_audit(
        db,
        action="error",
        resource=_AUDIT_RESOURCE_PARTE,
        resource_id=parte.id,
        actor=actor,
        metadata={"error_msg": parte.ocr_error_msg},
    )
    return parte


async def processar_ocr_parte_diaria(
    db: AsyncSession,
    parte_id: int,
    *,
    client: Any,
    storage: Any,
) -> ParteDiaria:
    """Roda OCR via Document AI e popula campos extraidos.

    Mesma estrategia do `consultar_detran`: NUNCA propaga excecao --
    qualquer falha (transporte, auth, formato inesperado) vira
    `ocr_status='erro'` com `ocr_error_msg`. UI permite reprocessar.
    """
    parte = await db.get(ParteDiaria, parte_id)
    if parte is None:
        raise ValueError(f"parte_diaria {parte_id} nao encontrada")
    if not parte.anexo_path:
        # Sem anexo nao tem o que processar -- pode acontecer se o
        # upload original falhou no meio. Marca erro e sai.
        parte.ocr_status = PARTE_ERRO
        parte.ocr_error_msg = "anexo ausente"
        await db.commit()
        await db.refresh(parte)
        return parte

    try:
        content = await storage.read(parte.anexo_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ocr parte_diaria %s: falha ao ler anexo: %s", parte_id, exc
        )
        parte.ocr_status = PARTE_ERRO
        parte.ocr_error_msg = f"falha ao ler anexo: {exc!s}"[:500]
        await db.commit()
        await _record_audit(
            db,
            action="error",
            resource=_AUDIT_RESOURCE_PARTE,
            resource_id=parte_id,
            metadata={"stage": "read_anexo", "error": str(exc)[:500]},
        )
        await db.refresh(parte)
        return parte

    try:
        result = await client.processar_documento(
            content=content,
            mime_type=parte.mime_type or "application/octet-stream",
            filename=parte.filename_original or f"parte-{parte_id}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ocr parte_diaria %s: Document AI falhou: %s", parte_id, exc
        )
        parte.ocr_status = PARTE_ERRO
        parte.ocr_error_msg = str(exc)[:500]
        parte.ocr_source = (
            "google_documentai_mock"
            if getattr(client, "is_mock", False)
            else "google_documentai"
        )
        await db.commit()
        await _record_audit(
            db,
            action="error",
            resource=_AUDIT_RESOURCE_PARTE,
            resource_id=parte_id,
            metadata={"stage": "documentai", "error": str(exc)[:500]},
        )
        await db.refresh(parte)
        return parte

    fields = result.get("fields") or {}
    parte.ocr_payload = result
    parte.ocr_source = result.get("source", "google_documentai")
    parte.ocr_confidence = result.get("confidence")
    parte.ocr_status = PARTE_PROCESSADO
    parte.ocr_error_msg = None

    # Pre-preenchimento dos campos canonicos -- operador revisa depois.
    if (data_v := _parse_iso_date_str(fields.get("data"))) is not None:
        parte.data = data_v
    if (op := fields.get("operador")):
        parte.operador = str(op)[:200]
    if (obra := fields.get("obra")):
        parte.obra = str(obra)[:200]
    if (eq := fields.get("equipamento")):
        parte.equipamento = str(eq)[:200]
    if (placa := fields.get("placa")):
        parte.placa = str(placa)[:8]
    if (h_ini := _coerce_horimetro(fields.get("horimetro_inicio"))) is not None:
        parte.horimetro_inicio = h_ini
    if (h_fim := _coerce_horimetro(fields.get("horimetro_fim"))) is not None:
        parte.horimetro_fim = h_fim
    if (k_ini := _coerce_int(fields.get("km_inicio"))) is not None:
        parte.km_inicio = k_ini
    if (k_fim := _coerce_int(fields.get("km_fim"))) is not None:
        parte.km_fim = k_fim

    # Tenta amarrar ao veiculo pela placa (best-effort).
    if parte.placa and parte.veiculo_id is None:
        v = await get_veiculo_by_placa(db, parte.placa)
        if v is not None:
            parte.veiculo_id = v.id

    await db.commit()
    await _record_audit(
        db,
        action="update",
        resource=_AUDIT_RESOURCE_PARTE,
        resource_id=parte.id,
        metadata={
            "stage": "ocr_processado",
            "source": parte.ocr_source,
            "confidence": float(parte.ocr_confidence or 0),
            "veiculo_id": parte.veiculo_id,
        },
    )
    await db.refresh(parte)
    return parte


async def update_parte_diaria(
    db: AsyncSession,
    parte_id: int,
    fields: dict[str, Any],
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> ParteDiaria | None:
    """Revisao manual dos campos extraidos.

    Marca como `revisado` automaticamente se status atual e
    `processado`/`erro` (operador conferiu). Audita `changed` com
    pares `{from, to}` -- mesmo padrao do update de Veiculo.
    """
    parte = await db.get(ParteDiaria, parte_id)
    if parte is None:
        return None

    changed: dict[str, dict[str, Any]] = {}
    explicit_status = fields.get("ocr_status")
    for key, new_val in fields.items():
        if not hasattr(parte, key):
            continue
        old_val = getattr(parte, key)
        if old_val == new_val:
            continue
        changed[key] = {
            "from": str(old_val) if old_val is not None else None,
            "to": str(new_val) if new_val is not None else None,
        }
        setattr(parte, key, new_val)

    # Se operador editou qualquer campo (exceto status explicito) e o
    # status atual e processado/erro, vira `revisado` automaticamente.
    auto_revisado = (
        not explicit_status
        and any(k != "ocr_status" for k in changed)
        and parte.ocr_status in {PARTE_PROCESSADO, PARTE_ERRO}
    )
    if auto_revisado:
        changed["ocr_status"] = {
            "from": parte.ocr_status,
            "to": PARTE_REVISADO,
        }
        parte.ocr_status = PARTE_REVISADO

    if not changed:
        return parte

    await db.commit()
    await _record_audit(
        db,
        action="update",
        resource=_AUDIT_RESOURCE_PARTE,
        resource_id=parte.id,
        actor=actor,
        metadata={"changed": changed},
    )
    await db.refresh(parte)
    return parte


async def delete_parte_diaria(
    db: AsyncSession,
    parte_id: int,
    *,
    storage: Any,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    """Apaga parte diaria + anexo no storage. Audita antes do delete."""
    parte = await db.get(ParteDiaria, parte_id)
    if parte is None:
        return False
    snapshot = {
        "anexo_path": parte.anexo_path,
        "filename": parte.filename_original,
        "data": str(parte.data) if parte.data else None,
        "veiculo_id": parte.veiculo_id,
    }
    if parte.anexo_path:
        try:
            await storage.delete(parte.anexo_path)
        except Exception as exc:  # noqa: BLE001
            # Nao bloqueamos o delete por orfao no storage; apenas
            # logamos. O DB e fonte de verdade.
            logger.warning(
                "parte_diaria %s: falha ao apagar anexo %s: %s",
                parte_id,
                parte.anexo_path,
                exc,
            )
    await db.delete(parte)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource=_AUDIT_RESOURCE_PARTE,
        resource_id=parte_id,
        actor=actor,
        metadata=snapshot,
    )
    return True


async def get_parte_diaria(
    db: AsyncSession, parte_id: int
) -> ParteDiaria | None:
    return await db.get(ParteDiaria, parte_id)


async def list_partes_diarias(
    db: AsyncSession,
    *,
    veiculo_id: int | None = None,
    obra: str | None = None,
    ocr_status: str | None = None,
    placa: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[ParteDiaria], int]:
    base = select(ParteDiaria)
    conditions = []
    if veiculo_id is not None:
        conditions.append(ParteDiaria.veiculo_id == veiculo_id)
    if obra:
        conditions.append(ParteDiaria.obra == obra)
    if ocr_status:
        conditions.append(ParteDiaria.ocr_status == ocr_status)
    if placa:
        conditions.append(ParteDiaria.placa == placa.upper())
    if conditions:
        base = base.where(*conditions)
    count_stmt = select(func.count()).select_from(base.subquery())
    list_stmt = (
        base.order_by(ParteDiaria.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    total = (await db.execute(count_stmt)).scalar_one()
    rows = list((await db.execute(list_stmt)).scalars().all())
    return rows, int(total)


# --- Document AI client factory + singleton --------------------------------


def get_documentai_client(settings: Any) -> Any:
    """Constroi um novo client real ou mock conforme configuracao.

    Mesma estrategia dos demais (Infosimples/Dominio/OneDrive). Se
    qualquer um dos 3 (credentials_json, project_id, processor_id)
    estiver vazio, o adapter cai no mock determinístico.
    """
    from app.integrations.google_documentai.client import (
        GoogleDocumentAIClient,
    )

    return GoogleDocumentAIClient(
        credentials_json=settings.google_documentai_credentials_json,
        project_id=settings.gcp_project_id,
        processor_id=settings.documentai_processor_id,
        location=settings.documentai_location,
    )


_documentai_singleton: Any | None = None


def get_documentai_singleton() -> Any:
    """Singleton por processo -- mesmo motivo do `infosimples_singleton`
    (reuso do pool TCP entre requests)."""
    global _documentai_singleton
    if _documentai_singleton is None:
        _documentai_singleton = get_documentai_client(_get_settings())
    return _documentai_singleton


def reset_documentai_singleton() -> Any | None:
    global _documentai_singleton
    prev = _documentai_singleton
    _documentai_singleton = None
    return prev


# --- Storage helper compartilhado API/worker (B.2) -------------------------
#
# Tanto o router quanto o worker `_run_ocr_parte_diaria` precisam abrir o
# storage de partes diarias respeitando `STORAGE_BACKEND`. Sem essa
# fatoracao, a API saving em OneDrive escrevia o item_id no DB e o worker
# tentava `LocalStorage.read(item_id)` -> FileNotFoundError. (Apontado
# pelo Devin Review.) Devolvemos como async context manager porque o
# OneDriveStorage segura um httpx client que precisa de aclose().

@contextlib.asynccontextmanager
async def open_partes_diarias_storage(settings: Any) -> AsyncIterator[Any]:
    """Abre o storage de partes diarias conforme `STORAGE_BACKEND`.

    Local: `LocalStorage` em `<editais_storage_parent>/<parte_diaria_subdir>`.
    OneDrive: `OneDriveStorage` apontando para a pasta
    `<parte_diaria_subdir>` no drive configurado.

    Uso:
        async with open_partes_diarias_storage(settings) as storage:
            await storage.read(path)
    """
    from app.integrations.onedrive.client import build_onedrive_client
    from app.integrations.onedrive.storage import OneDriveStorage
    from app.modules.licitacoes.storage import LocalStorage

    backend = (getattr(settings, "storage_backend", None) or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.parte_diaria_storage_subdir,
        )
        try:
            yield OneDriveStorage(client)
        finally:
            await client.aclose()
        return
    base = Path(settings.editais_storage_path).parent
    yield LocalStorage(base / settings.parte_diaria_storage_subdir)


# --- Celery dispatch (B.2) ------------------------------------------------
#
# AGENTS.md: "Scrapers/OCR/RPA rodam SEMPRE no worker (Celery) -- nunca
# bloqueiem a API." O endpoint POST /partes-diarias e o /reprocessar usam
# `enqueue_ocr_parte_diaria(parte_id)` que dispara `worker.tasks.manutencao.
# ocr_parte_diaria` por nome (sem importar o pacote `worker`, que esta em
# outro modulo do monorepo). A task usa a fila `manutencao` ja roteada em
# `worker.main.celery_app.conf.task_routes`.
#
# Em testes a gente monkeypatch isso para chamar
# `processar_ocr_parte_diaria` direto -- assim a assertiva de "campos
# preenchidos" continua valida sem precisar de Redis/celery rodando.

_celery_dispatcher_singleton: Any | None = None


def get_celery_dispatcher() -> Any:
    """Singleton do app Celery usado pela API APENAS para `send_task`.

    Cada `Celery(...)` aloca pool de conexoes (Redis/AMQP) interno. Como
    `enqueue_ocr_parte_diaria` e chamado em todo upload + reprocessar,
    instanciar por chamada vaza conexoes (apontado pelo Devin Review).
    Mantemos um singleton por processo da API, mesmo padrao do Infosimples
    /Dominio/OneDrive (so que esses sao httpx clients, nao Celery).
    """
    global _celery_dispatcher_singleton
    if _celery_dispatcher_singleton is None:
        from celery import Celery

        settings = _get_settings()
        _celery_dispatcher_singleton = Celery(broker=settings.redis_url)
    return _celery_dispatcher_singleton


def reset_celery_dispatcher_singleton() -> Any | None:
    """Limpa o singleton do dispatcher Celery (testes / lifespan)."""
    global _celery_dispatcher_singleton
    prev = _celery_dispatcher_singleton
    _celery_dispatcher_singleton = None
    return prev


def enqueue_ocr_parte_diaria(parte_id: int) -> None:
    """Despacha OCR para o worker Celery (fila `manutencao`).

    Usa `send_task` por nome para evitar dependencia de import entre
    apps/api e apps/workers. Caso o broker nao esteja disponivel
    (caso raro -- tipicamente Redis down), a chamada levanta
    `kombu.exceptions.OperationalError`. A row ja foi criada com
    `ocr_status='pendente'`, entao retentar o upload nao causa duplicidade.

    Reusa um app Celery singleton (`get_celery_dispatcher`) para nao
    alocar pool TCP novo a cada upload.
    """
    dispatcher = get_celery_dispatcher()
    dispatcher.send_task(
        "worker.tasks.manutencao.ocr_parte_diaria",
        args=[parte_id],
        queue="manutencao",
    )


__all__ = [
    "add_documento",
    "consultar_detran",
    "create_parte_diaria",
    "create_veiculo",
    "delete_documento",
    "delete_parte_diaria",
    "delete_veiculo",
    "enqueue_ocr_parte_diaria",
    "get_celery_dispatcher",
    "get_documentai_client",
    "get_documentai_singleton",
    "get_infosimples_client",
    "get_infosimples_singleton",
    "get_parte_diaria",
    "get_veiculo",
    "get_veiculo_by_placa",
    "list_consultas_detran",
    "list_partes_diarias",
    "list_veiculos",
    "mark_parte_diaria_erro",
    "open_partes_diarias_storage",
    "processar_ocr_parte_diaria",
    "reset_celery_dispatcher_singleton",
    "reset_documentai_singleton",
    "reset_infosimples_singleton",
    "update_documento",
    "update_parte_diaria",
    "update_veiculo",
]
