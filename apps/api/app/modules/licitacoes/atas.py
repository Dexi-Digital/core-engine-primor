"""Squad 3 / D.9: ingestao de atas de registro de preco do PNCP."""
from __future__ import annotations

import logging
import re
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.integrations.pncp.client import PncpAta, PncpClient
from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao
from app.modules.licitacoes.schemas import AtaIngestSummary

logger = logging.getLogger(__name__)

# "00394460000141-1-000156/2024" -> cnpj / sequencial / ano
_RE_NUMERO_CONTROLE = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")


def external_id_from_numero_controle(nc: str | None) -> str | None:
    """Converte numeroControlePNCPCompra no external_id usado em `licitacoes`."""
    m = _RE_NUMERO_CONTROLE.match(nc or "")
    if not m:
        return None
    cnpj, seq, ano = m.groups()
    return f"{cnpj}-{int(ano)}-{int(seq)}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


async def ingest_atas(
    db: AsyncSession,
    client: PncpClient,
    *,
    data_inicial: date | str,
    data_final: date | str,
    max_paginas: int | None = None,
) -> AtaIngestSummary:
    """Pagina `client.iter_atas` e upserta em `licitacoes_atas_rp`.

    `iter_atas` re-levanta (`reraise=True`) a excecao original apos
    esgotar os retries internos de `list_atas` -- uma pagina que falha
    persistentemente (rede/5xx) interromperia a geracao pra sempre se nao
    isolassemos aqui. Por isso o `async for` inteiro fica dentro de um
    try/except: se uma pagina falha, paramos de paginar (nao ha como
    retomar de onde parou sem reimplementar a paginacao manualmente) mas
    COMMITAMOS o que ja foi processado nas paginas anteriores -- mesmo
    espirito de isolamento de falha de `service.py::ingest_publicacoes` e
    `resultados.py::ingest_resultados`, so que aqui o "commit parcial" e
    por pagina em vez de por modalidade/licitacao.
    """
    total = gravadas = atualizadas = vinculadas = falhas = 0
    try:
        async for ata in client.iter_atas(
            data_inicial=data_inicial, data_final=data_final, max_paginas=max_paginas
        ):
            if not ata.numero_controle_pncp_ata:
                continue
            total += 1
            licitacao_id = await _find_licitacao_id(db, ata)
            if licitacao_id is not None:
                vinculadas += 1

            existing = await db.scalar(
                select(AtaRegistroPreco).where(
                    AtaRegistroPreco.numero_controle_pncp_ata
                    == ata.numero_controle_pncp_ata
                )
            )
            values = dict(
                numero_ata=ata.numero_ata,
                ano_ata=ata.ano_ata,
                licitacao_id=licitacao_id,
                orgao_cnpj=ata.cnpj_orgao,
                orgao_nome=ata.nome_orgao,
                objeto=ata.objeto_contratacao,
                vigencia_inicio=_parse_date(ata.vigencia_inicio),
                vigencia_fim=_parse_date(ata.vigencia_fim),
                cancelado=ata.cancelado,
                possibilidade_adesao=ata.possibilidade_adesao,
                raw=ata.raw or None,
            )
            if existing is None:
                db.add(
                    AtaRegistroPreco(
                        numero_controle_pncp_ata=ata.numero_controle_pncp_ata, **values
                    )
                )
                gravadas += 1
            else:
                # so conta como atualizada se algum campo de fato mudou --
                # mesma convencao de `resultados.py::_upsert_resultado`,
                # pra reprocessar uma janela sem mudancas no PNCP nao
                # inflar o contador de `atualizadas`.
                changed = False
                for key, value in values.items():
                    if getattr(existing, key) != value:
                        setattr(existing, key, value)
                        changed = True
                if changed:
                    atualizadas += 1
    except (httpx.HTTPError, RetryError) as exc:
        logger.warning(
            "atas: paginacao interrompida (parcial) apos %s atas: %s",
            total,
            exc,
            exc_info=False,
        )
        falhas += 1

    await db.commit()
    return AtaIngestSummary(
        total_fetched=total,
        gravadas=gravadas,
        atualizadas=atualizadas,
        vinculadas=vinculadas,
        falhas=falhas,
    )


async def _find_licitacao_id(db: AsyncSession, ata: PncpAta) -> int | None:
    external_id = external_id_from_numero_controle(ata.numero_controle_pncp_compra)
    if external_id is None:
        return None
    return await db.scalar(
        select(Licitacao.id).where(Licitacao.external_id == external_id)
    )
