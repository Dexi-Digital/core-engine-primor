"""Edital download service (D.4).

Given a `Licitacao.id`, fetch every attached document (edital, projetos,
termos de referencia ...) from PNCP, persist them with `EditaisStorage`
and record the result in `licitacoes_editais` + `licitacoes_editais_anexos`.

The service is strictly idempotent: re-running the same licitacao does
not duplicate files -- existing `(edital_id, sequencial_documento)`
pairs are skipped.

The PNCP primary adapter covers all post-2023 contratacoes available on
the Portal Nacional. When PNCP has no attachments (empty list), the
service records `status="empty"` and leaves fallback wiring for future
ComprasNet / Licitacoes-e callers to extend without touching this file.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpArquivo, PncpClient
from app.modules.licitacoes.models import AnexoEdital, Edital, Licitacao
from app.modules.licitacoes.schemas import EditalDownloadResult
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)


async def get_edital(
    db: AsyncSession, licitacao_id: int
) -> Edital | None:
    """Fetch the stored edital row for a licitacao (without anexos joined)."""
    stmt = select(Edital).where(Edital.licitacao_id == licitacao_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_anexos(db: AsyncSession, edital_id: int) -> list[AnexoEdital]:
    stmt = (
        select(AnexoEdital)
        .where(AnexoEdital.edital_id == edital_id)
        .order_by(AnexoEdital.sequencial_documento)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_or_create_edital(
    db: AsyncSession, *, licitacao_id: int, source: str
) -> Edital:
    existing = await get_edital(db, licitacao_id)
    if existing is not None:
        existing.source = source
        existing.status = "pending"
        existing.error_message = None
        await db.flush()
        return existing
    edital = Edital(licitacao_id=licitacao_id, source=source, status="pending")
    db.add(edital)
    await db.flush()
    return edital


async def _existing_sequenciais(db: AsyncSession, edital_id: int) -> set[int]:
    stmt = select(AnexoEdital.sequencial_documento).where(
        AnexoEdital.edital_id == edital_id
    )
    result = await db.execute(stmt)
    return {int(r) for r in result.scalars().all()}


async def download_edital_for_licitacao(
    db: AsyncSession,
    *,
    licitacao_id: int,
    pncp: PncpClient,
    storage: EditaisStorage,
) -> EditalDownloadResult:
    """Download every PNCP-published document for `licitacao_id`.

    Returns a summary with `status`:
      * "completed" -- at least one file downloaded (or all already present)
      * "empty"     -- PNCP had no documents for this contratacao
      * "failed"    -- PNCP error, left for retry; edital row stays pending
    """
    licitacao = await db.get(Licitacao, licitacao_id)
    if licitacao is None:
        raise ValueError(f"Licitacao {licitacao_id} not found")
    if not (licitacao.orgao_cnpj and licitacao.ano_compra and licitacao.sequencial_compra):
        raise ValueError(
            f"Licitacao {licitacao_id} is missing PNCP triplet (cnpj/ano/sequencial)"
        )

    edital = await _get_or_create_edital(db, licitacao_id=licitacao_id, source="pncp")

    try:
        arquivos = await pncp.list_arquivos(
            cnpj=licitacao.orgao_cnpj,
            ano=licitacao.ano_compra,
            sequencial=licitacao.sequencial_compra,
        )
    except httpx.HTTPError as exc:
        logger.warning("pncp list_arquivos failed for licitacao %s: %s", licitacao_id, exc)
        edital.status = "failed"
        edital.error_message = f"pncp list_arquivos: {exc}"
        await db.commit()
        return EditalDownloadResult(
            licitacao_id=licitacao_id,
            source="pncp",
            status="failed",
            anexos_count=edital.anexos_count,
            new_anexos=0,
            error_message=edital.error_message,
        )

    if not arquivos:
        edital.status = "empty"
        edital.anexos_count = 0
        await db.commit()
        return EditalDownloadResult(
            licitacao_id=licitacao_id,
            source="pncp",
            status="empty",
            anexos_count=0,
            new_anexos=0,
        )

    already = await _existing_sequenciais(db, edital.id)
    new_count = 0
    for arq in arquivos:
        if arq.sequencial_documento in already:
            continue
        try:
            await _download_single(
                db, edital_id=edital.id, licitacao_id=licitacao_id,
                pncp=pncp, storage=storage, arquivo=arq,
            )
            new_count += 1
        except httpx.HTTPError as exc:
            logger.warning(
                "pncp download failed for licitacao %s seq %s: %s",
                licitacao_id,
                arq.sequencial_documento,
                exc,
            )
            # Keep going -- other files for the same contratacao may work.
            continue

    total = await _existing_sequenciais(db, edital.id)
    edital.anexos_count = len(total)
    edital.status = "completed" if edital.anexos_count > 0 else "failed"
    edital.error_message = None if edital.status == "completed" else "no files downloaded"
    await db.commit()

    return EditalDownloadResult(
        licitacao_id=licitacao_id,
        source="pncp",
        status=edital.status,
        anexos_count=edital.anexos_count,
        new_anexos=new_count,
        error_message=edital.error_message,
    )


async def _download_single(
    db: AsyncSession,
    *,
    edital_id: int,
    licitacao_id: int,
    pncp: PncpClient,
    storage: EditaisStorage,
    arquivo: PncpArquivo,
) -> None:
    """Stream one arquivo to storage and insert the AnexoEdital row."""
    if not arquivo.url:
        raise ValueError("PncpArquivo has empty url")

    chunks, server_filename, content_type = await pncp.stream_arquivo(arquivo.url)
    # Prefer the PNCP-declared title over the opaque Content-Disposition
    # filename (which is a UUID on PNCP, eg "uivd1biu.pdf"). We still keep
    # the server extension when the titulo has none.
    filename = _choose_filename(arquivo.titulo, server_filename)
    storage_path, size = await storage.save(
        licitacao_id=licitacao_id, filename=filename, content=chunks
    )
    db.add(
        AnexoEdital(
            edital_id=edital_id,
            sequencial_documento=arquivo.sequencial_documento,
            titulo=arquivo.titulo,
            tipo_documento=arquivo.tipo_documento_descricao,
            source_url=arquivo.url,
            filename=filename,
            storage_path=storage_path,
            size_bytes=size,
            content_type=content_type,
        )
    )
    await db.flush()


def _choose_filename(titulo: str | None, server_filename: str) -> str:
    """Pick a human-friendly filename, preserving the server extension."""
    server_ext = ""
    if "." in server_filename:
        server_ext = "." + server_filename.rsplit(".", 1)[1].lower()
    if not titulo:
        return server_filename
    base = titulo.strip()
    if "." in base:
        return base
    return f"{base}{server_ext}" if server_ext else base
