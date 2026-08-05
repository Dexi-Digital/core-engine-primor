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
from app.modules.licitacoes.schemas import ConcorrenteRow, GeotargetingRow


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
