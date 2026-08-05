"""Squad 3: resultados/homologacoes + atas RP + dashboards comerciais."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao, ResultadoLicitacao
from app.modules.licitacoes.resultados import ingest_resultados


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
        # Relativa a `now`: o service filtra por janela `dias` a partir de
        # `datetime.now(UTC)`, entao um valor fixo ficaria obsoleto com o
        # tempo (ver test_ingest_resultados_*).
        data_publicacao_pncp=datetime.now(UTC) - timedelta(days=5),
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


def _pncp_portal_client(handler) -> PncpClient:
    http = httpx.AsyncClient(
        base_url="https://mockportal.test", transport=httpx.MockTransport(handler)
    )
    return PncpClient(portal_base_url="https://mockportal.test", portal_client=http)


def _itens_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/itens"):
        return httpx.Response(
            200,
            json=[
                {"numeroItem": 1, "descricao": "Recapeamento", "temResultado": True},
                {"numeroItem": 2, "descricao": "Sinalizacao", "temResultado": False},
            ],
        )
    assert url.endswith("/itens/1/resultados")  # item 2 nunca deve ser consultado
    return httpx.Response(
        200,
        json=[
            {
                "sequencialResultado": 1,
                "niFornecedor": "11222333000144",
                "nomeRazaoSocialFornecedor": "Construtora Alfa LTDA",
                "valorTotalHomologado": 1450000.0,
                "dataResultado": "2026-07-10",
                "situacaoCompraItemResultadoNome": "Informado",
            }
        ],
    )


@pytest.mark.asyncio
async def test_ingest_resultados_grava_e_e_idempotente(db_session: AsyncSession) -> None:
    lic = await _make_licitacao(db_session)
    await db_session.commit()

    client = _pncp_portal_client(_itens_handler)
    summary = await ingest_resultados(db_session, client)
    assert summary.licitacoes_processadas == 1
    assert summary.com_resultado == 1
    assert summary.resultados_gravados == 1
    assert summary.falhas == 0

    # segunda rodada: mesmo payload nao duplica
    client2 = _pncp_portal_client(_itens_handler)
    summary2 = await ingest_resultados(db_session, client2)
    assert summary2.resultados_gravados == 0

    rows = (await db_session.execute(select(ResultadoLicitacao))).scalars().all()
    assert len(rows) == 1
    assert rows[0].licitacao_id == lic.id
    assert rows[0].valor_homologado == Decimal("1450000.00")
    await client.aclose()
    await client2.aclose()


@pytest.mark.asyncio
async def test_ingest_resultados_isola_falha_por_licitacao(db_session: AsyncSession) -> None:
    await _make_licitacao(db_session)
    await _make_licitacao(
        db_session,
        external_id="99888777000166-2024-9",
        orgao_cnpj="99888777000166",
        ano_compra=2024,
        sequencial_compra=9,
    )
    await db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/99888777000166/" in url:
            return httpx.Response(500)
        return _itens_handler(request)

    client = _pncp_portal_client(handler)
    summary = await ingest_resultados(db_session, client)
    assert summary.licitacoes_processadas == 2
    assert summary.falhas == 1
    assert summary.resultados_gravados == 1
    await client.aclose()


def _itens_handler_situacao_atualizada(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/itens"):
        return httpx.Response(
            200,
            json=[
                {"numeroItem": 1, "descricao": "Recapeamento", "temResultado": True},
                {"numeroItem": 2, "descricao": "Sinalizacao", "temResultado": False},
            ],
        )
    assert url.endswith("/itens/1/resultados")
    return httpx.Response(
        200,
        json=[
            {
                "sequencialResultado": 1,
                "niFornecedor": "11222333000144",
                "nomeRazaoSocialFornecedor": "Construtora Alfa LTDA",
                "valorTotalHomologado": 1600000.0,
                "dataResultado": "2026-07-10",
                "situacaoCompraItemResultadoNome": "Homologado",
            }
        ],
    )


@pytest.mark.asyncio
async def test_ingest_resultados_atualiza_situacao_em_reprocessamento(
    db_session: AsyncSession,
) -> None:
    """Upsert de verdade: a situacao evolui no PNCP (Informado -> Homologado)
    e o reprocessamento precisa refletir o estado mais recente, nao
    congelar o primeiro valor gravado."""
    await _make_licitacao(db_session)
    await db_session.commit()

    client = _pncp_portal_client(_itens_handler)
    await ingest_resultados(db_session, client)
    await client.aclose()

    client2 = _pncp_portal_client(_itens_handler_situacao_atualizada)
    summary2 = await ingest_resultados(db_session, client2)
    await client2.aclose()

    assert summary2.resultados_gravados == 1

    rows = (await db_session.execute(select(ResultadoLicitacao))).scalars().all()
    assert len(rows) == 1
    assert rows[0].valor_homologado == Decimal("1600000.00")
    assert rows[0].situacao == "Homologado"


def _itens_handler_dois_itens_com_resultado(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/itens"):
        return httpx.Response(
            200,
            json=[
                {"numeroItem": 1, "descricao": "Recapeamento", "temResultado": True},
                {"numeroItem": 2, "descricao": "Sinalizacao", "temResultado": True},
            ],
        )
    if url.endswith("/itens/1/resultados"):
        return httpx.Response(
            200,
            json=[
                {
                    "sequencialResultado": 1,
                    "niFornecedor": "11222333000144",
                    "nomeRazaoSocialFornecedor": "Construtora Alfa LTDA",
                    "valorTotalHomologado": 1450000.0,
                    "dataResultado": "2026-07-10",
                    "situacaoCompraItemResultadoNome": "Informado",
                }
            ],
        )
    assert url.endswith("/itens/2/resultados")
    return httpx.Response(500)


@pytest.mark.asyncio
async def test_ingest_resultados_conta_com_resultado_e_falha_em_falha_parcial(
    db_session: AsyncSession,
) -> None:
    """Item 1 grava com sucesso; item 2 falha na rede -- a licitacao deve
    aparecer em `com_resultado` (dado parcial persistido) E em `falhas`
    (overlap intencional -- ver docstring de `ResultadoIngestSummary`)."""
    await _make_licitacao(db_session)
    await db_session.commit()

    client = _pncp_portal_client(_itens_handler_dois_itens_com_resultado)
    summary = await ingest_resultados(db_session, client)
    await client.aclose()

    assert summary.licitacoes_processadas == 1
    assert summary.resultados_gravados == 1
    assert summary.com_resultado == 1
    assert summary.falhas == 1

    rows = (await db_session.execute(select(ResultadoLicitacao))).scalars().all()
    assert len(rows) == 1
    assert rows[0].item_numero == 1


@pytest.mark.asyncio
async def test_ingest_resultados_endpoint_requires_auth(api_client) -> None:
    resp = await api_client.post("/api/v1/licitacoes/ingest/resultados")
    assert resp.status_code == 401
