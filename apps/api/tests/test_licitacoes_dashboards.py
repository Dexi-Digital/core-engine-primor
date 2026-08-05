"""Squad 3: dashboards comerciais (concorrentes, geotargeting)."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.dashboards import (
    dashboard_concorrentes,
    dashboard_eficiencia,
    dashboard_geotargeting,
    dashboard_nao_captados,
)
from app.modules.licitacoes.models import (
    DecisaoTriagem,  # Squad 1
    Licitacao,
    ResultadoLicitacao,
)


async def _seed(db: AsyncSession) -> None:
    lics = [
        Licitacao(
            external_id=f"111-{2026}-{i}",
            source="pncp",
            orgao_cnpj=f"0039446000014{i}",
            orgao_razao_social=f"Prefeitura {i}",
            uf_sigla="MG",
            municipio_nome="Belo Horizonte" if i == 1 else "Uberlandia",
            modalidade_nome="Concorrencia Eletronica",
            data_publicacao_pncp=datetime(2026, 7, i, tzinfo=UTC),
        )
        for i in (1, 2)
    ]
    db.add_all(lics)
    await db.flush()
    db.add_all(
        [
            ResultadoLicitacao(
                licitacao_id=lics[0].id,
                item_numero=1,
                sequencial_resultado=1,
                cnpj_vencedor="11222333000144",
                razao_social="Construtora Alfa LTDA",
                valor_homologado=Decimal("1000000.00"),
                data_resultado=datetime(2026, 7, 10, tzinfo=UTC),
            ),
            ResultadoLicitacao(
                licitacao_id=lics[1].id,
                item_numero=1,
                sequencial_resultado=1,
                cnpj_vencedor="11222333000144",
                razao_social="Construtora Alfa LTDA",
                valor_homologado=Decimal("500000.00"),
                data_resultado=datetime(2026, 7, 12, tzinfo=UTC),
            ),
            ResultadoLicitacao(
                licitacao_id=lics[1].id,
                item_numero=2,
                sequencial_resultado=1,
                cnpj_vencedor="55666777000188",
                razao_social="Construtora Beta LTDA",
                valor_homologado=Decimal("200000.00"),
                data_resultado=datetime(2026, 7, 12, tzinfo=UTC),
            ),
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_dashboard_concorrentes_agrega_por_cnpj(db_session: AsyncSession) -> None:
    await _seed(db_session)
    rows = await dashboard_concorrentes(db_session)
    assert len(rows) == 2
    alfa = rows[0]  # ordenado por valor desc
    assert alfa.cnpj == "11222333000144"
    assert alfa.razao_social == "Construtora Alfa LTDA"
    assert alfa.licitacoes_vencidas == 2
    assert alfa.valor_total_homologado == Decimal("1500000.00")
    assert alfa.orgaos_distintos == 2


@pytest.mark.asyncio
async def test_dashboard_geotargeting_agrupa_municipio(db_session: AsyncSession) -> None:
    await _seed(db_session)
    rows = await dashboard_geotargeting(db_session, uf="MG")
    por_municipio = {r.municipio: r for r in rows}
    assert por_municipio["Uberlandia"].valor_total_homologado == Decimal("700000.00")
    assert por_municipio["Belo Horizonte"].licitacoes_com_resultado == 1


@pytest.mark.asyncio
async def test_dashboards_endpoints_respondem(api_client, db_session) -> None:
    await _seed(db_session)
    r1 = await api_client.get("/api/v1/licitacoes/dashboards/concorrentes")
    assert r1.status_code == 200
    assert r1.json()[0]["cnpj"] == "11222333000144"
    r2 = await api_client.get("/api/v1/licitacoes/dashboards/geotargeting?uf=MG")
    assert r2.status_code == 200
    assert len(r2.json()) == 2


@pytest.mark.asyncio
async def test_dashboard_nao_captados_conta_por_status(db_session: AsyncSession) -> None:
    for i, status in enumerate(["rejeitado", "rejeitado", "sem_planilha", "erro_portal", "completo"]):
        db_session.add(
            Licitacao(
                external_id=f"nc-{i}",
                source="pncp",
                uf_sigla="MG",
                status_triagem=status,
                data_publicacao_pncp=datetime(2026, 7, 1 + i, tzinfo=UTC),
            )
        )
    await db_session.commit()

    resp = await dashboard_nao_captados(db_session)
    assert resp.por_status["rejeitado"] == 2
    assert resp.por_status["sem_planilha"] == 1
    assert resp.por_status["erro_portal"] == 1
    assert "completo" not in resp.por_status
    assert resp.total == 4


@pytest.mark.asyncio
async def test_dashboard_nao_captados_vazio(db_session: AsyncSession) -> None:
    resp = await dashboard_nao_captados(db_session)
    assert resp.total == 0
    assert resp.por_status == {}


@pytest.mark.asyncio
async def test_dashboard_eficiencia(db_session: AsyncSession) -> None:
    lic = Licitacao(
        external_id="ef-1", source="pncp", uf_sigla="MG", status_triagem="completo",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    lic2 = Licitacao(
        external_id="ef-2", source="pncp", uf_sigla="MG", status_triagem="sem_planilha",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db_session.add_all([lic, lic2])
    await db_session.flush()
    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id, decisao="aprovado", usuario_email="ana@primor.com"
        )
    )
    await db_session.commit()

    resp = await dashboard_eficiencia(db_session)
    assert resp.total_triadas == 1
    assert resp.pct_com_planilha == 50.0


@pytest.mark.asyncio
async def test_dashboard_eficiencia_vazio(db_session: AsyncSession) -> None:
    resp = await dashboard_eficiencia(db_session)
    assert resp.total_triadas == 0
    assert resp.tempo_medio_triagem_horas is None
    assert resp.pct_com_planilha is None
    assert resp.falhas_por_status == {}


@pytest.mark.asyncio
async def test_dashboards_nao_captados_e_eficiencia_endpoints(
    api_client, db_session
) -> None:
    r1 = await api_client.get("/api/v1/licitacoes/dashboards/nao-captados")
    assert r1.status_code == 200
    assert r1.json() == {"total": 0, "por_status": {}}

    r2 = await api_client.get("/api/v1/licitacoes/dashboards/eficiencia")
    assert r2.status_code == 200
    assert r2.json()["total_triadas"] == 0
