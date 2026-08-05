"""Squad 3: resultados/homologacoes + atas RP + dashboards comerciais."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao, ResultadoLicitacao


async def _make_licitacao(db: AsyncSession, **overrides) -> Licitacao:
    defaults = dict(
        external_id="00394460000141-2024-156",
        source="pncp",
        numero_compra="156",
        ano_compra=2024,
        sequencial_compra=156,
        objeto_compra="Recapeamento asfaltico",
        modalidade_nome="Concorrencia Eletronica",
        orgao_cnpj="00394460000141",
        orgao_razao_social="Prefeitura X",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    defaults.update(overrides)
    lic = Licitacao(**defaults)
    db.add(lic)
    await db.flush()
    return lic


@pytest.mark.asyncio
async def test_resultado_and_ata_models_roundtrip(db_session: AsyncSession) -> None:
    lic = await _make_licitacao(db_session)
    db_session.add(
        ResultadoLicitacao(
            licitacao_id=lic.id,
            item_numero=1,
            sequencial_resultado=1,
            cnpj_vencedor="11222333000144",
            razao_social="Construtora Alfa LTDA",
            valor_homologado=Decimal("1450000.00"),
            data_resultado=datetime(2026, 7, 10, tzinfo=UTC),
            situacao="Informado",
            porte_fornecedor="Demais",
        )
    )
    db_session.add(
        AtaRegistroPreco(
            numero_controle_pncp_ata="00394460000141-1-000156/2024-001",
            numero_ata="001/2026",
            licitacao_id=lic.id,
            orgao_cnpj="00394460000141",
            orgao_nome="Prefeitura X",
            vigencia_inicio=datetime(2026, 1, 1, tzinfo=UTC).date(),
            vigencia_fim=datetime(2026, 12, 31, tzinfo=UTC).date(),
            objeto="Registro de precos de pavimentacao",
            possibilidade_adesao=True,
        )
    )
    await db_session.commit()

    r = (await db_session.execute(select(ResultadoLicitacao))).scalar_one()
    assert r.cnpj_vencedor == "11222333000144"
    a = (await db_session.execute(select(AtaRegistroPreco))).scalar_one()
    assert a.licitacao_id == lic.id
