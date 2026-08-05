"""Squad 3: dashboards comerciais (concorrentes, geotargeting)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
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
async def test_dashboards_ignoram_resultado_cancelado(db_session: AsyncSession) -> None:
    """Resultado com `situacao` contendo "cancel" nao entra nas somas.

    Sem o filtro, o item cancelado abaixo inflaria o total da Alfa em
    +9.000.000 e o de Belo Horizonte na mesma medida.
    """
    await _seed(db_session)
    lic = (
        await db_session.execute(
            select(Licitacao).where(Licitacao.municipio_nome == "Belo Horizonte")
        )
    ).scalar_one()
    db_session.add(
        ResultadoLicitacao(
            licitacao_id=lic.id,
            item_numero=9,
            sequencial_resultado=1,
            cnpj_vencedor="11222333000144",
            razao_social="Construtora Alfa LTDA",
            valor_homologado=Decimal("9000000.00"),
            situacao="Cancelado",
            data_resultado=datetime(2026, 7, 15, tzinfo=UTC),
        )
    )
    await db_session.commit()

    concorrentes = {r.cnpj: r for r in await dashboard_concorrentes(db_session)}
    alfa = concorrentes["11222333000144"]
    assert alfa.valor_total_homologado == Decimal("1500000.00")
    assert alfa.licitacoes_vencidas == 2

    geo = {r.municipio: r for r in await dashboard_geotargeting(db_session, uf="MG")}
    assert geo["Belo Horizonte"].valor_total_homologado == Decimal("1000000.00")
    assert geo["Belo Horizonte"].licitacoes_com_resultado == 1


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
async def test_dashboard_eficiencia_tempo_medio_aritmetica(
    db_session: AsyncSession,
) -> None:
    """Deltas fixos de 2h e 4h -> media exata de 3.0 horas."""
    captado_em = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    lics = [
        Licitacao(
            external_id=f"tm-{i}",
            source="pncp",
            uf_sigla="MG",
            data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
            created_at=captado_em,
        )
        for i in (1, 2)
    ]
    db_session.add_all(lics)
    await db_session.flush()
    db_session.add_all(
        [
            DecisaoTriagem(
                licitacao_id=lics[0].id,
                decisao="aprovado",
                usuario_email="ana@primor.com",
                created_at=captado_em + timedelta(hours=2),
            ),
            DecisaoTriagem(
                licitacao_id=lics[1].id,
                decisao="rejeitado",
                usuario_email="ana@primor.com",
                created_at=captado_em + timedelta(hours=4),
            ),
        ]
    )
    await db_session.commit()

    resp = await dashboard_eficiencia(db_session)
    assert resp.total_triadas == 2
    assert resp.tempo_medio_triagem_horas == 3.0


@pytest.mark.asyncio
async def test_dashboards_nao_captados_e_eficiencia_filtram_por_uf(
    db_session: AsyncSession,
) -> None:
    """`uf` filtra as duas pernas do funil (triagem e planilha)."""
    lic_mg = Licitacao(
        external_id="uf-mg", source="pncp", uf_sigla="MG", status_triagem="rejeitado",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    lic_sp = Licitacao(
        external_id="uf-sp", source="pncp", uf_sigla="SP", status_triagem="rejeitado",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    lic_mg_completo = Licitacao(
        external_id="uf-mg-2", source="pncp", uf_sigla="MG", status_triagem="completo",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    lic_sp_sem = Licitacao(
        external_id="uf-sp-2", source="pncp", uf_sigla="SP",
        status_triagem="sem_planilha",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db_session.add_all([lic_mg, lic_sp, lic_mg_completo, lic_sp_sem])
    await db_session.flush()
    db_session.add_all(
        [
            DecisaoTriagem(
                licitacao_id=lic_mg.id, decisao="rejeitado",
                usuario_email="ana@primor.com",
            ),
            DecisaoTriagem(
                licitacao_id=lic_sp.id, decisao="rejeitado",
                usuario_email="ana@primor.com",
            ),
        ]
    )
    await db_session.commit()

    nc = await dashboard_nao_captados(db_session, uf="MG")
    assert nc.total == 1
    assert nc.por_status == {"rejeitado": 1}
    assert (await dashboard_nao_captados(db_session)).total == 3

    ef = await dashboard_eficiencia(db_session, uf="mg")  # case-insensitive
    assert ef.total_triadas == 1
    # so lic_mg_completo entra no denominador; o sem_planilha de SP fica fora
    assert ef.pct_com_planilha == 100.0
    assert (await dashboard_eficiencia(db_session)).pct_com_planilha == 50.0


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


@pytest.mark.asyncio
async def test_dashboards_endpoints_aceitam_filtro_uf(api_client, db_session) -> None:
    db_session.add_all(
        [
            Licitacao(
                external_id="ep-mg", source="pncp", uf_sigla="MG",
                status_triagem="rejeitado",
                data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
            ),
            Licitacao(
                external_id="ep-sp", source="pncp", uf_sigla="SP",
                status_triagem="rejeitado",
                data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.commit()

    r1 = await api_client.get("/api/v1/licitacoes/dashboards/nao-captados?uf=MG")
    assert r1.status_code == 200
    assert r1.json() == {"total": 1, "por_status": {"rejeitado": 1}}

    r2 = await api_client.get("/api/v1/licitacoes/dashboards/eficiencia?uf=SP")
    assert r2.status_code == 200
    assert r2.json()["falhas_por_status"] == {}
