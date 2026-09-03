"""Ingestao dos lancamentos do TOTVS RM.

Roda SO no worker (ver `worker/tasks/financeiro.py`), sequencial e sob
lock single-flight. Nunca na API: licenca de WebService do RM e
consumida por requisicao e concorrencia aqui esgota o pool de licencas
da operacao.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.totvs.client import TotvsClient
from app.integrations.totvs.extractor import TotvsPermissionError
from app.modules.financeiro_totvs.models import TotvsLancamento, TotvsSyncLog

logger = logging.getLogger(__name__)

# Campos do envelope do extractor que viram coluna. `source` do
# envelope vira `extractor` na tabela (em `totvs_sync_log`, `source`
# quer dizer outra coisa: quem disparou o pull).
_COLUNAS = (
    "external_id",
    "codcoligada",
    "codfilial",
    "idlan",
    "valor",
    "contraparte_documento",
    "contraparte_nome",
    "data_vencimento",
    "data_emissao",
    "status_rm",
    "raw",
)


@dataclass(frozen=True)
class SyncResumo:
    janela: str
    source: str
    extractor: str
    lidos: int
    gravados: int
    skipped: bool = False


def janela_de(desde: date, ate: date) -> str:
    return f"{desde.isoformat()}..{ate.isoformat()}"


def _to_row(envelope: dict[str, Any]) -> dict[str, Any]:
    row = {campo: envelope.get(campo) for campo in _COLUNAS}
    row["extractor"] = envelope.get("source")
    return row


async def _upsert(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Upsert por `external_id`. ON CONFLICT no Postgres (prod), merge no SQLite (testes).

    Mesmo desenho do `licitacoes.service._upsert` (PNCP) -- decisao #4.
    """
    if not rows:
        return 0

    dialect: Dialect = db.bind.dialect  # type: ignore[assignment]
    if dialect.name == "postgresql":
        stmt = pg_insert(TotvsLancamento).values(rows)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in TotvsLancamento.__table__.columns
            if c.name not in ("id", "external_id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["external_id"], set_=update_cols
        )
        result = await db.execute(stmt)
        return result.rowcount or len(rows)

    gravados = 0
    for row in rows:
        existente = await db.scalar(
            select(TotvsLancamento).where(
                TotvsLancamento.external_id == row["external_id"]
            )
        )
        if existente is None:
            db.add(TotvsLancamento(**row))
        else:
            for chave, valor in row.items():
                if chave != "external_id":
                    setattr(existente, chave, valor)
        gravados += 1
    return gravados


async def _log_sync(
    db: AsyncSession,
    *,
    source: str,
    janela: str,
    extractor: str,
    status: str,
    lidos: int = 0,
    gravados: int = 0,
    error_message: str | None = None,
) -> None:
    """Grava/atualiza a linha de (source, janela) -- in-place no retry."""
    log = await db.scalar(
        select(TotvsSyncLog).where(
            TotvsSyncLog.source == source, TotvsSyncLog.janela == janela
        )
    )
    if log is None:
        log = TotvsSyncLog(source=source, janela=janela, extractor=extractor)
        db.add(log)
    log.extractor = extractor
    log.status = status
    log.lidos = lidos
    log.gravados = gravados
    log.error_message = error_message[:2048] if error_message else None
    await db.commit()


async def _verificar_coligadas(
    client: TotvsClient, esperadas: set[int]
) -> None:
    """Falha se o perfil nao enxerga TODAS as coligadas esperadas.

    Extractor que nao sabe listar coligadas (ex.: wsConsultaSQL, que
    roda na HttpPort e nao tem esse endpoint) devolve None -- ai a
    guarda e pulada com aviso, em vez de dar falso negativo.
    """
    visiveis = await client.list_coligadas()
    if visiveis is None:
        logger.warning(
            "totvs: extractor %s nao lista coligadas; guarda de filtro "
            "silencioso NAO aplicada nesta execucao",
            client.source,
        )
        return
    faltando = esperadas - visiveis
    if faltando:
        raise TotvsPermissionError(
            "perfil do usuario nao enxerga a(s) coligada(s) "
            f"{sorted(faltando)} -- esperadas {sorted(esperadas)}, "
            f"visiveis {sorted(visiveis)}. A API do RM FILTRA em vez de "
            "dar erro, entao seguir aqui produziria leitura parcial "
            "passando por completa."
        )


async def ingest_lancamentos(
    db: AsyncSession,
    client: TotvsClient,
    *,
    desde: date,
    ate: date,
    source: str = "beat",
    coligadas_esperadas: set[int] | None = None,
) -> SyncResumo:
    """Le a janela no RM e upserta em `totvs_lancamentos`.

    `source` ("beat" | "manual") entra na unique key do log de execucao,
    entao um disparo manual NAO suprime a execucao agendada da mesma
    janela -- ver decisao #5 e a docstring de
    `dispatch_contrato_alerts_endpoint`.

    Re-executar e sempre seguro: o upsert e idempotente por
    `external_id`. Nao ha "janela ja feita, pulando" aqui de proposito;
    `skipped` existe no resumo para o caller que decidir pular (ex.:
    lock nao adquirido), nao para suprimir trabalho.

    `coligadas_esperadas` liga a guarda contra o FILTRO SILENCIOSO do
    RM. Confirmado pela TOTVS em 27/08/2026: um perfil sem permissao
    numa coligada nao recebe erro -- a API devolve 200 com apenas os
    registros das coligadas permitidas. Sem a guarda, um perfil
    apertado demais produz uma leitura parcial que passa por completa e
    a reconciliacao fecha "certo" em cima de metade dos lancamentos.
    Opt-in: so roda quando o operador declara o que espera enxergar.
    """
    janela = janela_de(desde, ate)
    extractor = client.source
    try:
        if coligadas_esperadas:
            await _verificar_coligadas(client, coligadas_esperadas)
        envelopes = await client.fetch_lancamentos(desde=desde, ate=ate)
    except Exception as exc:  # noqa: BLE001 -- relogado e re-levantado
        await _log_sync(
            db,
            source=source,
            janela=janela,
            extractor=extractor,
            status="failed",
            error_message=str(exc),
        )
        logger.exception("totvs: pull falhou janela=%s source=%s", janela, source)
        raise

    rows = [_to_row(e) for e in envelopes]
    gravados = await _upsert(db, rows)
    await db.commit()
    await _log_sync(
        db,
        source=source,
        janela=janela,
        extractor=extractor,
        status="ok",
        lidos=len(rows),
        gravados=gravados,
    )
    return SyncResumo(
        janela=janela,
        source=source,
        extractor=extractor,
        lidos=len(rows),
        gravados=gravados,
    )
