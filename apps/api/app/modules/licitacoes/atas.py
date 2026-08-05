"""Squad 3 / D.9: ingestao de atas de registro de preco do PNCP."""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpAta, PncpClient
from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao
from app.modules.licitacoes.schemas import AtaIngestSummary

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
    total = gravadas = atualizadas = vinculadas = 0
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
                AtaRegistroPreco.numero_controle_pncp_ata == ata.numero_controle_pncp_ata
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
            for key, value in values.items():
                setattr(existing, key, value)
            atualizadas += 1

    await db.commit()
    return AtaIngestSummary(
        total_fetched=total,
        gravadas=gravadas,
        atualizadas=atualizadas,
        vinculadas=vinculadas,
    )


async def _find_licitacao_id(db: AsyncSession, ata: PncpAta) -> int | None:
    external_id = external_id_from_numero_controle(ata.numero_controle_pncp_compra)
    if external_id is None:
        return None
    return await db.scalar(
        select(Licitacao.id).where(Licitacao.external_id == external_id)
    )
