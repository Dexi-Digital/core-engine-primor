"""Business logic for the Licitacoes module."""
from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import MODALIDADES, PncpClient, PncpPublicacao
from app.modules.licitacoes.models import Licitacao
from app.modules.licitacoes.schemas import IngestResult

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # PNCP returns ISO-8601 timestamps ("2024-10-01T12:34:56").
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def _publicacao_to_row(pub: PncpPublicacao) -> dict[str, Any]:
    orgao = pub.orgao
    unidade = pub.unidade
    return {
        "external_id": pub.external_id,
        "source": "pncp",
        "numero_compra": pub.numero_compra,
        "ano_compra": pub.ano_compra,
        "sequencial_compra": pub.sequencial_compra,
        "objeto_compra": pub.objeto_compra,
        "modalidade_nome": pub.modalidade_nome,
        "modo_disputa_nome": pub.modo_disputa_nome,
        "situacao_compra_nome": pub.situacao_compra_nome,
        "valor_total_estimado": pub.valor_total_estimado,
        "valor_total_homologado": pub.valor_total_homologado,
        "srp": pub.srp,
        "orgao_cnpj": orgao.cnpj if orgao else None,
        "orgao_razao_social": orgao.razao_social if orgao else None,
        "uf_sigla": unidade.uf_sigla if unidade else None,
        "municipio_nome": unidade.municipio_nome if unidade else None,
        "codigo_ibge": unidade.codigo_ibge if unidade else None,
        "data_publicacao_pncp": _parse_dt(pub.data_publicacao_pncp),
        "data_atualizacao_pncp": _parse_dt(pub.data_atualizacao),
        "raw": pub.raw if isinstance(pub.raw, dict) else asdict(pub) if is_dataclass(pub) else None,
    }


async def _upsert(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Upsert rows by `external_id`. Returns how many were written.

    Uses ON CONFLICT on Postgres (which is what prod runs on). On SQLite
    (used by tests) falls back to a merge loop.
    """
    if not rows:
        return 0

    dialect: Dialect = db.bind.dialect  # type: ignore[assignment]
    if dialect.name == "postgresql":
        stmt = pg_insert(Licitacao).values(rows)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in Licitacao.__table__.columns
            if c.name not in ("id", "external_id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(index_elements=["external_id"], set_=update_cols)
        result = await db.execute(stmt)
        return result.rowcount or len(rows)

    written = 0
    for row in rows:
        existing = await db.scalar(
            select(Licitacao).where(Licitacao.external_id == row["external_id"])
        )
        if existing is None:
            db.add(Licitacao(**row))
        else:
            for key, value in row.items():
                if key != "external_id":
                    setattr(existing, key, value)
        written += 1
    return written


async def ingest_publicacoes(
    db: AsyncSession,
    client: PncpClient,
    *,
    data_inicial: date | str,
    data_final: date | str,
    uf: str | None = None,
    modalidades: tuple[int, ...] = MODALIDADES,
    tamanho_pagina: int = 50,
    max_paginas: int | None = None,
) -> IngestResult:
    """Iterate over PNCP publicacoes across all modalidades and upsert them.

    Returns aggregated stats. This is the entrypoint consumed by both the
    Celery worker (`crawler_pncp`) and on-demand API calls.
    """
    total_fetched = 0
    pending: list[dict[str, Any]] = []
    failed: list[int] = []

    for modalidade in modalidades:
        try:
            async for pub in client.iter_contratacoes_por_publicacao(
                data_inicial=data_inicial,
                data_final=data_final,
                codigo_modalidade=modalidade,
                uf=uf,
                tamanho_pagina=tamanho_pagina,
                max_paginas=max_paginas,
            ):
                total_fetched += 1
                pending.append(_publicacao_to_row(pub))
                # flush in batches to keep memory bounded for very large windows
                if len(pending) >= 500:
                    await _upsert(db, pending)
                    pending.clear()
        except httpx.HTTPError as exc:
            # Isolate flakiness: if the PNCP times out or returns 5xx for one
            # modalidade, record it and keep going so the other modalidades
            # still land in the DB. Callers can re-queue the failed ones.
            logger.warning(
                "pncp modalidade %s failed: %s", modalidade, exc, exc_info=False
            )
            failed.append(modalidade)

    written = 0
    if pending:
        written = await _upsert(db, pending)
    await db.commit()

    # We don't distinguish inserted vs updated precisely on the PG path
    # (ON CONFLICT merges). Leaving both counters for the SQLite path where
    # we could track it — for now report `written` as inserted.
    return IngestResult(
        inserted=written,
        updated=0,
        skipped=total_fetched - written,
        total_fetched=total_fetched,
        failed_modalidades=failed,
    )


async def list_licitacoes(
    db: AsyncSession,
    *,
    uf: str | None = None,
    modalidade: str | None = None,
    orgao_cnpj: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Licitacao], int]:
    stmt = select(Licitacao)
    count_stmt = select(func.count()).select_from(Licitacao)

    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
        count_stmt = count_stmt.where(Licitacao.uf_sigla == uf.upper())
    if modalidade:
        stmt = stmt.where(Licitacao.modalidade_nome.ilike(f"%{modalidade}%"))
        count_stmt = count_stmt.where(Licitacao.modalidade_nome.ilike(f"%{modalidade}%"))
    if orgao_cnpj:
        stmt = stmt.where(Licitacao.orgao_cnpj == orgao_cnpj)
        count_stmt = count_stmt.where(Licitacao.orgao_cnpj == orgao_cnpj)

    stmt = (
        stmt.order_by(Licitacao.data_publicacao_pncp.desc().nulls_last())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    total = (await db.execute(count_stmt)).scalar_one()
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def get_licitacao(db: AsyncSession, licitacao_id: int) -> Licitacao | None:
    return await db.get(Licitacao, licitacao_id)
