"""API-level tests for Licitacoes endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.licitacoes.models import Licitacao


@pytest.mark.asyncio
async def test_status_endpoint(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/licitacoes/status")
    assert r.status_code == 200
    body = r.json()
    assert body["module"] == "licitacoes"
    assert body["implemented"] is True


@pytest.mark.asyncio
async def test_list_empty(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/licitacoes")
    assert r.status_code == 200
    body = r.json()
    assert body == {"total": 0, "page": 1, "page_size": 20, "data": []}


@pytest.mark.asyncio
async def test_list_and_get_detail(api_client: AsyncClient, db_session) -> None:
    lic = Licitacao(
        external_id="12345678-2024-1",
        source="pncp",
        numero_compra="2024-0001",
        ano_compra=2024,
        sequencial_compra=1,
        objeto_compra="Obra de pavimentacao",
        modalidade_nome="Pregao Eletronico",
        orgao_cnpj="12345678000100",
        orgao_razao_social="Prefeitura Teste",
        uf_sigla="SP",
        municipio_nome="Cidade",
    )
    db_session.add(lic)
    await db_session.commit()

    r = await api_client.get("/api/v1/licitacoes?uf=SP")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["external_id"] == "12345678-2024-1"

    r2 = await api_client.get(f"/api/v1/licitacoes/{lic.id}")
    assert r2.status_code == 200
    assert r2.json()["objeto_compra"] == "Obra de pavimentacao"

    r3 = await api_client.get("/api/v1/licitacoes/99999")
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_list_search_param(api_client: AsyncClient, db_session) -> None:
    db_session.add_all(
        [
            Licitacao(
                external_id="srch-1",
                source="pncp",
                objeto_compra="Pavimentacao de estradas rurais",
                uf_sigla="MG",
            ),
            Licitacao(
                external_id="srch-2",
                source="pncp",
                objeto_compra="Merenda escolar para rede municipal",
                uf_sigla="MG",
            ),
        ]
    )
    await db_session.commit()

    r = await api_client.get("/api/v1/licitacoes?search=estradas")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["external_id"] == "srch-1"


@pytest.mark.asyncio
async def test_list_filter_no_match(api_client: AsyncClient, db_session) -> None:
    db_session.add(
        Licitacao(
            external_id="12345678-2024-2",
            source="pncp",
            uf_sigla="SP",
            modalidade_nome="Pregao Eletronico",
        )
    )
    await db_session.commit()
    r = await api_client.get("/api/v1/licitacoes?uf=RJ")
    assert r.status_code == 200
    assert r.json()["total"] == 0
