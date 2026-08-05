"""Camada de servico do modulo fiscal: persistencia + envio Domínio."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.integrations.dominio.client import (
    DominioAuthError,
    DominioError,
    DominioMockClient,
)
from app.modules.fiscal.models import (
    STATUS_ENVIO_VALIDOS,
    DocumentoFiscal,
    DocumentoFiscalItem,
)
from app.modules.fiscal.parser import (
    FiscalParseError,
    parse_nfe_detalhes,
    parse_xml,
)
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)


# Igual ao padrao do dp_sesmt: AGENTS.md exige que toda mutacao de
# recurso sensivel grave em audit_log. XMLs fiscais contem CNPJs +
# valores -- LGPD/contabil. Toda criacao/envio/erro precisa virar uma
# linha em audit_log.
_AUDIT_RESOURCE = "fiscal.documento"
# Default usado em paths sem usuario logado (worker de retry, importacao
# automatica do escritorio contabil etc.). Mutacoes vindas de requests
# HTTP devem passar `actor=current_user.email` -- ver router.
_AUDIT_ACTOR_PLACEHOLDER = _AUDIT_ACTOR_SYSTEM


class FiscalDuplicateError(ValueError):
    """O XML ja foi importado (mesmo (tipo, chave_acesso) ou xml_hash)."""

    def __init__(self, message: str, *, existing_id: int) -> None:
        super().__init__(message)
        self.existing_id = existing_id


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


async def _stream_bytes(payload: bytes) -> AsyncIterator[bytes]:
    """Adapta `bytes` para o contrato AsyncIterator de `EditaisStorage.save()`."""
    yield payload


async def import_xml(
    db: AsyncSession,
    *,
    xml_bytes: bytes,
    storage: EditaisStorage,
    filename: str = "documento.xml",
    source: str | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> DocumentoFiscal:
    """Recebe bytes de um XML, parseia, persiste storage + DB.

    Levanta:
      - `FiscalParseError`: XML invalido ou sem campos minimos
      - `FiscalDuplicateError`: XML ja importado (idempotencia)
    """
    parsed = parse_xml(xml_bytes)

    # 2o passe: impostos/UF/itens (so NF-e/NFC-e; None para os demais).
    detalhes = (
        parse_nfe_detalhes(xml_bytes)
        if parsed.tipo in {"nfe", "nfce"}
        else None
    )

    # Idempotencia ANTES de gravar no storage. Evita arquivo orfao.
    existing = await _find_duplicate(
        db,
        tipo=parsed.tipo,
        chave_acesso=parsed.chave_acesso,
        xml_hash=parsed.xml_hash,
    )
    if existing is not None:
        raise FiscalDuplicateError(
            f"documento ja importado (id={existing.id}, "
            f"tipo={parsed.tipo}, chave={parsed.chave_acesso})",
            existing_id=existing.id,
        )

    # `EditaisStorage.save` exige `licitacao_id` (legado do D.4); usamos
    # o hash do XML como bucket logico para nao misturar fiscal com
    # licitacoes -- o storage local cria subdir por id, entao usar um
    # numero estavel derivado do hash mantem fiscal/<bucket>/file.xml.
    #
    # ATENCAO: o `bucket` sozinho NAO garante unicidade do `target` no
    # disco -- 100_000 valores possiveis caem em birthday-paradox a ~316
    # documentos. Pior: o filename do upload tipicamente repete (default
    # "documento.xml" ou "doc.xml" do ERP), entao bucket-collision +
    # filename igual = sobrescrita silenciosa. Por isso prefixamos o
    # filename com o hash completo abreviado (16 hex chars = 64 bits
    # de entropia, colisao em ~4 bilhoes de docs). Combinado com o
    # UNIQUE em xml_hash no DB, a sobrescrita fica matematicamente
    # impossivel sem violar tambem a constraint de duplicata.
    bucket = int(parsed.xml_hash[:8], 16) % 100_000
    safe_filename = f"{parsed.xml_hash[:16]}-{filename}"
    storage_path, _size = await storage.save(
        licitacao_id=bucket,
        filename=safe_filename,
        content=_stream_bytes(xml_bytes),
    )

    doc = DocumentoFiscal(
        tipo=parsed.tipo,
        chave_acesso=parsed.chave_acesso,
        numero=parsed.numero,
        serie=parsed.serie,
        emitente_cnpj=parsed.emitente_cnpj,
        emitente_nome=parsed.emitente_nome,
        destinatario_cnpj=parsed.destinatario_cnpj,
        destinatario_nome=parsed.destinatario_nome,
        valor_total=parsed.valor_total,
        data_emissao=parsed.data_emissao,
        uf=detalhes.uf if detalhes else None,
        chave_dv_valida=detalhes.chave_dv_valida if detalhes else None,
        valor_icms=detalhes.valor_icms if detalhes else None,
        valor_ipi=detalhes.valor_ipi if detalhes else None,
        valor_pis=detalhes.valor_pis if detalhes else None,
        valor_cofins=detalhes.valor_cofins if detalhes else None,
        xml_path=storage_path,
        xml_hash=parsed.xml_hash,
        status_envio="pendente",
        retry_count=0,
        source=source,
    )
    db.add(doc)
    try:
        await db.flush()  # materializa doc.id para os itens
        if detalhes:
            for item in detalhes.itens:
                db.add(
                    DocumentoFiscalItem(
                        documento_id=doc.id,
                        ordem=item.ordem,
                        codigo=item.codigo,
                        descricao=item.descricao,
                        ncm=item.ncm,
                        cfop=item.cfop,
                        unidade=item.unidade,
                        quantidade=item.quantidade,
                        valor_unitario=item.valor_unitario,
                        valor_total=item.valor_total,
                    )
                )
        await db.commit()
    except IntegrityError as exc:
        # Race entre verificacao e commit -- duplicado paralelo. Limpa
        # o arquivo orfao e devolve o existente.
        await db.rollback()
        try:
            await storage.delete(storage_path)
        except Exception:  # noqa: BLE001
            logger.warning("falha ao limpar XML orfao em %s", storage_path)
        existing = await _find_duplicate(
            db,
            tipo=parsed.tipo,
            chave_acesso=parsed.chave_acesso,
            xml_hash=parsed.xml_hash,
        )
        raise FiscalDuplicateError(
            f"documento duplicado em race: {exc.orig}",
            existing_id=existing.id if existing else 0,
        ) from exc
    await db.refresh(doc)
    await _record_audit(
        db,
        action="create",
        resource_id=doc.id,
        actor=actor,
        metadata={
            "tipo": doc.tipo,
            "chave_acesso": doc.chave_acesso,
            "emitente_cnpj": doc.emitente_cnpj,
            "valor_total": doc.valor_total,
            "source": doc.source,
            "itens_count": len(detalhes.itens) if detalhes else 0,
        },
    )
    return doc


async def _find_duplicate(
    db: AsyncSession,
    *,
    tipo: str,
    chave_acesso: str | None,
    xml_hash: str,
) -> DocumentoFiscal | None:
    if chave_acesso:
        stmt = select(DocumentoFiscal).where(
            (DocumentoFiscal.tipo == tipo)
            & (DocumentoFiscal.chave_acesso == chave_acesso)
        )
        existing = await db.scalar(stmt)
        if existing is not None:
            return existing
    stmt = select(DocumentoFiscal).where(DocumentoFiscal.xml_hash == xml_hash)
    return await db.scalar(stmt)


async def get_documento(
    db: AsyncSession, doc_id: int
) -> DocumentoFiscal | None:
    return await db.get(DocumentoFiscal, doc_id)


async def list_itens(
    db: AsyncSession, documento_id: int
) -> Sequence[DocumentoFiscalItem]:
    stmt = (
        select(DocumentoFiscalItem)
        .where(DocumentoFiscalItem.documento_id == documento_id)
        .order_by(DocumentoFiscalItem.ordem)
    )
    result = await db.scalars(stmt)
    return result.all()


async def list_documentos(
    db: AsyncSession,
    *,
    tipo: str | None = None,
    status_envio: str | None = None,
    emitente_cnpj: str | None = None,
    destinatario_cnpj: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[DocumentoFiscal]:
    stmt = select(DocumentoFiscal)
    if tipo:
        stmt = stmt.where(DocumentoFiscal.tipo == tipo)
    if status_envio:
        stmt = stmt.where(DocumentoFiscal.status_envio == status_envio)
    if emitente_cnpj:
        stmt = stmt.where(DocumentoFiscal.emitente_cnpj == emitente_cnpj)
    if destinatario_cnpj:
        stmt = stmt.where(DocumentoFiscal.destinatario_cnpj == destinatario_cnpj)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                DocumentoFiscal.chave_acesso.ilike(like),
                DocumentoFiscal.numero.ilike(like),
                DocumentoFiscal.emitente_nome.ilike(like),
                DocumentoFiscal.destinatario_nome.ilike(like),
            )
        )
    stmt = stmt.order_by(desc(DocumentoFiscal.created_at)).offset(offset).limit(
        limit
    )
    result = await db.scalars(stmt)
    return result.all()


async def update_documento(
    db: AsyncSession,
    doc_id: int,
    *,
    observacoes: str | None = None,
    status_envio: str | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> DocumentoFiscal | None:
    doc = await get_documento(db, doc_id)
    if doc is None:
        return None
    changed: dict[str, Any] = {}
    if observacoes is not None and observacoes != doc.observacoes:
        changed["observacoes"] = {
            "from": doc.observacoes,
            "to": observacoes,
        }
        doc.observacoes = observacoes
    if status_envio is not None and status_envio != doc.status_envio:
        if status_envio not in STATUS_ENVIO_VALIDOS:
            raise ValueError(
                f"status_envio invalido: {status_envio!r}. "
                f"Validos: {sorted(STATUS_ENVIO_VALIDOS)}"
            )
        changed["status_envio"] = {
            "from": doc.status_envio,
            "to": status_envio,
        }
        doc.status_envio = status_envio
    if not changed:
        return doc
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        action="update",
        resource_id=doc.id,
        actor=actor,
        metadata={"changed": changed},
    )
    return doc


async def delete_documento(
    db: AsyncSession,
    doc_id: int,
    *,
    storage: EditaisStorage,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    doc = await get_documento(db, doc_id)
    if doc is None:
        return False
    snapshot = {
        "tipo": doc.tipo,
        "chave_acesso": doc.chave_acesso,
        "emitente_cnpj": doc.emitente_cnpj,
        "valor_total": doc.valor_total,
        "status_envio": doc.status_envio,
    }
    xml_path = doc.xml_path
    await db.delete(doc)
    await db.commit()
    try:
        await storage.delete(xml_path)
    except Exception:  # noqa: BLE001
        logger.warning(
            "falha ao remover XML do storage em %s (DB ja apagou)", xml_path
        )
    await _record_audit(
        db,
        action="delete",
        resource_id=doc_id,
        actor=actor,
        metadata=snapshot,
    )
    return True


async def enviar_para_dominio(
    db: AsyncSession,
    doc_id: int,
    *,
    dominio_client: Any,
    storage: EditaisStorage,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> DocumentoFiscal:
    """Envia o XML do documento para a Dominio.

    Atualiza `status_envio`, `protocolo_dominio`, `sent_at`, `error_msg`
    e `retry_count`. Em caso de erro, NAO re-levanta -- atualiza o
    registro e retorna; quem agenda o retry e a task do worker.

    `dominio_client` e qualquer client que implemente `upload_xml(...)`
    -- DominioClient real ou DominioMockClient.
    """
    doc = await get_documento(db, doc_id)
    if doc is None:
        raise ValueError(f"documento {doc_id} nao encontrado")

    doc.status_envio = "enviando"
    await db.commit()

    try:
        xml_bytes = await storage.read(doc.xml_path)
    except (FileNotFoundError, OSError) as exc:
        doc.status_envio = "erro"
        doc.error_msg = f"XML nao encontrado no storage: {exc}"
        doc.retry_count += 1
        await db.commit()
        await _record_audit(
            db,
            action="enviar_erro",
            resource_id=doc.id,
            actor=actor,
            metadata={"error": doc.error_msg, "retry_count": doc.retry_count},
        )
        return doc

    filename = f"{doc.tipo}-{doc.chave_acesso or doc.xml_hash[:16]}.xml"

    try:
        result = await dominio_client.upload_xml(
            filename=filename, content=xml_bytes, tipo=doc.tipo
        )
    except DominioAuthError as exc:
        doc.status_envio = "erro"
        doc.error_msg = f"auth Dominio: {exc}"
        doc.retry_count += 1
        await db.commit()
        await _record_audit(
            db,
            action="enviar_erro",
            resource_id=doc.id,
            actor=actor,
            metadata={
                "error": doc.error_msg,
                "auth": True,
                "retry_count": doc.retry_count,
            },
        )
        return doc
    except DominioError as exc:
        doc.status_envio = "erro"
        doc.error_msg = f"dominio: {exc}"
        doc.retry_count += 1
        await db.commit()
        await _record_audit(
            db,
            action="enviar_erro",
            resource_id=doc.id,
            actor=actor,
            metadata={"error": doc.error_msg, "retry_count": doc.retry_count},
        )
        return doc

    doc.status_envio = "enviado"
    doc.protocolo_dominio = str(result.get("protocolo") or "")
    doc.sent_at = datetime.now(tz=UTC)
    doc.error_msg = None
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        action="enviar_ok",
        resource_id=doc.id,
        actor=actor,
        metadata={
            "protocolo": doc.protocolo_dominio,
            "tipo": doc.tipo,
            "valor_total": doc.valor_total,
        },
    )
    return doc


def get_dominio_client(settings: Any) -> Any:
    """Constroi um novo client real ou mock conforme configuracao.

    Mesma estrategia de DirectData/LLM: se faltar credencial, cai no
    mock determinístico para nao bloquear dev/CI. Em prod, basta
    configurar `DOMINIO_*` no env.

    Esta funcao e a "factory" -- cada chamada cria um client novo. Para
    uso na API HTTP use `get_dominio_singleton()` que cacheia para
    aproveitar o token-cache (a Domínio rate-limita /token).
    """
    from app.integrations.dominio.client import DominioClient

    if all(
        [
            settings.dominio_audit_url,
            settings.dominio_integracao,
            settings.dominio_client_id,
            settings.dominio_client_secret,
        ]
    ):
        return DominioClient(
            audit_url=settings.dominio_audit_url,
            integracao=settings.dominio_integracao,
            client_id=settings.dominio_client_id,
            client_secret=settings.dominio_client_secret,
            base_url=settings.dominio_base_url,
        )
    return DominioMockClient()


# Singleton de processo. Criar um novo `DominioClient` por request
# desperdica o token-cache (que existe justamente porque a Dominio
# rate-limita o `/token`) E cria um novo pool TCP do httpx a cada
# upload. A camada de API HTTP reutiliza esta instancia entre
# requests; o `aclose()` e disparado no lifespan handler do FastAPI.
# Workers Celery seguem usando `get_dominio_client()` (factory) porque
# rodam em processos separados com lifecycle proprio.
_dominio_singleton: Any | None = None


def get_dominio_singleton() -> Any:
    """Devolve um `DominioClient` (ou mock) compartilhado por processo.

    Cacheia para que o token OAuth2 e o pool de conexoes httpx fiquem
    vivos entre requests. Em testes, basta limpar via
    `reset_dominio_singleton()` ou usar `app.dependency_overrides` no
    `get_dominio_dep` -- o singleton nao bloqueia injecao de mocks.
    """
    global _dominio_singleton
    if _dominio_singleton is None:
        _dominio_singleton = get_dominio_client(_get_settings())
    return _dominio_singleton


def reset_dominio_singleton() -> Any | None:
    """Limpa o singleton (lifespan shutdown / testes). Devolve a
    instancia anterior para o caller poder chamar `aclose()`."""
    global _dominio_singleton
    prev = _dominio_singleton
    _dominio_singleton = None
    return prev


def _get_settings() -> Any:
    # Indireto para evitar import circular em tempo de modulo.
    from app.core.config import get_settings

    return get_settings()


__all__ = [
    "FiscalDuplicateError",
    "FiscalParseError",
    "delete_documento",
    "enviar_para_dominio",
    "get_documento",
    "get_dominio_client",
    "get_dominio_singleton",
    "import_xml",
    "list_documentos",
    "list_itens",
    "reset_dominio_singleton",
    "update_documento",
]
