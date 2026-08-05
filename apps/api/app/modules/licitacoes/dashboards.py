"""Squad 3: dashboards comerciais (secao 9 do Projeto Tecnico do Captador).

Agregacoes SQL diretas -- sem BI externo. Os dashboards "nao captados"
e "eficiencia" (que dependem do workflow de triagem das Squads 1/2)
vivem tambem aqui; ver Task 7 do plano.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import Licitacao, ResultadoLicitacao
from app.modules.licitacoes.schemas import (
    ConcorrenteRow,
    EficienciaResponse,
    GeotargetingRow,
    NaoCaptadosResponse,
)

NAO_CAPTADO_STATUSES = ("rejeitado", "sem_planilha", "erro_portal", "erro_sharepoint")


async def dashboard_concorrentes(
    db: AsyncSession,
    *,
    uf: str | None = None,
    data_inicial: datetime | None = None,
    data_final: datetime | None = None,
    limit: int = 50,
) -> list[ConcorrenteRow]:
    valor_total = func.sum(ResultadoLicitacao.valor_homologado)
    stmt = (
        select(
            ResultadoLicitacao.cnpj_vencedor.label("cnpj"),
            func.max(ResultadoLicitacao.razao_social).label("razao_social"),
            func.count(func.distinct(ResultadoLicitacao.licitacao_id)).label(
                "licitacoes_vencidas"
            ),
            valor_total.label("valor_total_homologado"),
            func.count(func.distinct(Licitacao.orgao_cnpj)).label("orgaos_distintos"),
            func.max(ResultadoLicitacao.data_resultado).label("ultima_vitoria"),
        )
        .join(Licitacao, Licitacao.id == ResultadoLicitacao.licitacao_id)
        .where(ResultadoLicitacao.cnpj_vencedor.is_not(None))
    )
    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
    if data_inicial:
        stmt = stmt.where(ResultadoLicitacao.data_resultado >= data_inicial)
    if data_final:
        stmt = stmt.where(ResultadoLicitacao.data_resultado <= data_final)
    stmt = (
        stmt.group_by(ResultadoLicitacao.cnpj_vencedor)
        .order_by(valor_total.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [ConcorrenteRow(**row._mapping) for row in rows]


async def dashboard_geotargeting(
    db: AsyncSession,
    *,
    uf: str | None = "MG",
    limit: int = 100,
) -> list[GeotargetingRow]:
    valor_total = func.sum(ResultadoLicitacao.valor_homologado)
    stmt = (
        select(
            Licitacao.uf_sigla.label("uf"),
            Licitacao.municipio_nome.label("municipio"),
            func.count(func.distinct(ResultadoLicitacao.licitacao_id)).label(
                "licitacoes_com_resultado"
            ),
            valor_total.label("valor_total_homologado"),
        )
        .join(Licitacao, Licitacao.id == ResultadoLicitacao.licitacao_id)
    )
    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
    stmt = (
        stmt.group_by(Licitacao.uf_sigla, Licitacao.municipio_nome)
        .order_by(valor_total.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [GeotargetingRow(**row._mapping) for row in rows]


async def dashboard_nao_captados(db: AsyncSession) -> NaoCaptadosResponse:
    """Contagem de licitacoes que nao avancaram na triagem/processamento.

    Ver Task 7 (Squad 3, pos Squads 1/2): status considerados "nao
    captados" sao rejeitado, sem_planilha, erro_portal, erro_sharepoint.
    """
    stmt = (
        select(Licitacao.status_triagem, func.count(Licitacao.id))
        .where(Licitacao.status_triagem.in_(NAO_CAPTADO_STATUSES))
        .group_by(Licitacao.status_triagem)
    )
    rows = (await db.execute(stmt)).all()
    por_status = {status: count for status, count in rows}
    return NaoCaptadosResponse(total=sum(por_status.values()), por_status=por_status)


async def dashboard_eficiencia(db: AsyncSession) -> EficienciaResponse:
    """Eficiencia do funil de triagem (Squad 1) + processamento (Squad 2).

    `tempo_medio_triagem_horas` e `pct_com_planilha` ficam `None` quando
    nao ha dados suficientes (divisao por zero tratada).
    """
    from app.modules.licitacoes.models import DecisaoTriagem  # Squad 1

    total_triadas = (
        await db.execute(select(func.count(func.distinct(DecisaoTriagem.licitacao_id))))
    ).scalar_one()

    # tempo medio captacao -> primeira decisao (em horas)
    tempos_stmt = (
        select(
            func.min(DecisaoTriagem.created_at).label("decidido_em"),
            Licitacao.created_at.label("captado_em"),
        )
        .join(Licitacao, Licitacao.id == DecisaoTriagem.licitacao_id)
        .group_by(DecisaoTriagem.licitacao_id, Licitacao.created_at)
    )
    pares = (await db.execute(tempos_stmt)).all()
    deltas = [
        (row.decidido_em - row.captado_em).total_seconds() / 3600
        for row in pares
        if row.decidido_em and row.captado_em
    ]
    tempo_medio = round(sum(deltas) / len(deltas), 2) if deltas else None

    completo = (
        await db.execute(
            select(func.count()).where(Licitacao.status_triagem == "completo")
        )
    ).scalar_one()
    sem_planilha = (
        await db.execute(
            select(func.count()).where(Licitacao.status_triagem == "sem_planilha")
        )
    ).scalar_one()
    processadas = completo + sem_planilha
    pct = round(100.0 * completo / processadas, 2) if processadas else None

    falhas_stmt = (
        select(Licitacao.status_triagem, func.count(Licitacao.id))
        .where(Licitacao.status_triagem.in_(("erro_portal", "erro_sharepoint")))
        .group_by(Licitacao.status_triagem)
    )
    falhas = {status: count for status, count in (await db.execute(falhas_stmt)).all()}

    return EficienciaResponse(
        total_triadas=total_triadas,
        tempo_medio_triagem_horas=tempo_medio,
        pct_com_planilha=pct,
        falhas_por_status=falhas,
    )
