"""Squad 3: ingestao de resultados homologados a partir do PNCP.

Para cada licitacao ja captada (janela recente), consulta os itens na
API portal; itens com `temResultado=True` tem seus resultados baixados
e upsertados em `licitacoes_resultados`. Falha em uma licitacao nao
aborta as demais (mesmo espirito do ingest de publicacoes).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.models import Licitacao, ResultadoLicitacao
from app.modules.licitacoes.schemas import ResultadoIngestSummary
from app.modules.licitacoes.service import _parse_dt

logger = logging.getLogger(__name__)


def _as_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def ingest_resultados(
    db: AsyncSession,
    client: PncpClient,
    *,
    dias: int = 30,
    uf: str | None = None,
    max_licitacoes: int = 200,
) -> ResultadoIngestSummary:
    cutoff = datetime.now(UTC) - timedelta(days=dias)
    stmt = (
        select(Licitacao)
        .where(Licitacao.data_publicacao_pncp >= cutoff)
        .where(Licitacao.orgao_cnpj.is_not(None))
        .where(Licitacao.ano_compra.is_not(None))
        .where(Licitacao.sequencial_compra.is_not(None))
    )
    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
    stmt = stmt.order_by(Licitacao.data_publicacao_pncp.desc()).limit(max_licitacoes)
    licitacoes = list((await db.execute(stmt)).scalars().all())

    com_resultado = 0
    gravados = 0
    falhas = 0

    for lic in licitacoes:
        try:
            itens = await client.list_itens(
                cnpj=lic.orgao_cnpj, ano=lic.ano_compra, sequencial=lic.sequencial_compra
            )
            achou = False
            for item in itens:
                if not item.tem_resultado:
                    continue
                resultados = await client.list_item_resultados(
                    cnpj=lic.orgao_cnpj,
                    ano=lic.ano_compra,
                    sequencial=lic.sequencial_compra,
                    numero_item=item.numero_item,
                )
                for res in resultados:
                    achou = True
                    gravados += await _upsert_resultado(db, lic.id, item.numero_item, res)
            if achou:
                com_resultado += 1
        except (httpx.HTTPError, RetryError) as exc:
            logger.warning(
                "resultados: licitacao %s falhou: %s", lic.external_id, exc, exc_info=False
            )
            falhas += 1

    await db.commit()
    return ResultadoIngestSummary(
        licitacoes_processadas=len(licitacoes),
        com_resultado=com_resultado,
        resultados_gravados=gravados,
        falhas=falhas,
    )


async def _upsert_resultado(db, licitacao_id, item_numero, res) -> int:
    """Insere se (licitacao, item, sequencial) inedito; retorna 1 se gravou."""
    existing = await db.scalar(
        select(ResultadoLicitacao).where(
            ResultadoLicitacao.licitacao_id == licitacao_id,
            ResultadoLicitacao.item_numero == item_numero,
            ResultadoLicitacao.sequencial_resultado == res.sequencial_resultado,
        )
    )
    if existing is not None:
        return 0
    db.add(
        ResultadoLicitacao(
            licitacao_id=licitacao_id,
            item_numero=item_numero,
            sequencial_resultado=res.sequencial_resultado,
            cnpj_vencedor=res.ni_fornecedor,
            razao_social=res.nome_razao_social_fornecedor,
            valor_homologado=_as_decimal(res.valor_total_homologado),
            valor_unitario=_as_decimal(res.valor_unitario_homologado),
            quantidade=_as_decimal(res.quantidade_homologada),
            data_resultado=_parse_dt(res.data_resultado),
            situacao=res.situacao_nome,
            porte_fornecedor=res.porte_fornecedor_nome,
            raw=res.raw or None,
        )
    )
    await db.flush()
    return 1
