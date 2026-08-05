"""Unit tests for PncpClient using a mocked httpx transport."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.pncp.client import PncpClient, PncpPublicacao


def _sample_payload(page: int, paginas_restantes: int, n: int = 2) -> dict:
    return {
        "data": [
            {
                "numeroCompra": f"2024-00{i + page * 10}",
                "anoCompra": 2024,
                "sequencialCompra": i + page * 10,
                "objetoCompra": f"Aquisicao de pecas {i}",
                "modalidadeNome": "Pregao Eletronico",
                "modoDisputaNome": "Aberto",
                "situacaoCompraNome": "Divulgada no PNCP",
                "valorTotalEstimado": 1000.5 + i,
                "valorTotalHomologado": None,
                "srp": False,
                "dataAtualizacao": "2024-10-01T12:34:56",
                "dataPublicacaoPncp": "2024-10-01T12:00:00",
                "orgaoEntidade": {
                    "cnpj": "12345678000100",
                    "razaoSocial": "Prefeitura Municipal de Fulano",
                    "poderId": "E",
                    "esferaId": "M",
                },
                "unidadeOrgao": {
                    "ufSigla": "SP",
                    "ufNome": "Sao Paulo",
                    "municipioNome": "Fulano de Tal",
                    "codigoIbge": "3550308",
                    "nomeUnidade": "Secretaria de Obras",
                    "codigoUnidade": "001",
                },
            }
            for i in range(n)
        ],
        "totalRegistros": 4,
        "totalPaginas": 2,
        "numeroPagina": page,
        "paginasRestantes": paginas_restantes,
        "empty": False,
    }


@pytest.mark.asyncio
async def test_list_contratacoes_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/contratacoes/publicacao")
        params = dict(request.url.params)
        assert params["dataInicial"] == "20250401"
        assert params["dataFinal"] == "20250402"
        assert params["codigoModalidadeContratacao"] == "6"
        assert params["uf"] == "SP"
        return httpx.Response(200, json=_sample_payload(page=1, paginas_restantes=1))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://mock.test", transport=transport)
    client = PncpClient(base_url="https://mock.test", client=http)

    page = await client.list_contratacoes_por_publicacao(
        data_inicial="2025-04-01",
        data_final="2025-04-02",
        codigo_modalidade=6,
        uf="SP",
        tamanho_pagina=2,
    )
    assert page.total_registros == 4
    assert len(page.data) == 2
    first = page.data[0]
    assert isinstance(first, PncpPublicacao)
    assert first.orgao and first.orgao.cnpj == "12345678000100"
    assert first.unidade and first.unidade.uf_sigla == "SP"
    assert first.external_id.startswith("12345678000100-2024-")

    await client.aclose()


@pytest.mark.asyncio
async def test_iter_paginates_until_exhausted() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        pagina = int(dict(request.url.params)["pagina"])
        if pagina == 1:
            return httpx.Response(200, json=_sample_payload(page=1, paginas_restantes=1))
        if pagina == 2:
            return httpx.Response(200, json=_sample_payload(page=2, paginas_restantes=0))
        raise AssertionError(f"unexpected page {pagina}")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://mock.test", transport=transport)
    client = PncpClient(base_url="https://mock.test", client=http)

    results = [
        item
        async for item in client.iter_contratacoes_por_publicacao(
            data_inicial="2025-04-01",
            data_final="2025-04-02",
            codigo_modalidade=6,
            tamanho_pagina=2,
        )
    ]
    assert calls["n"] == 2
    assert len(results) == 4

    await client.aclose()


@pytest.mark.asyncio
async def test_204_returns_empty_page() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(204))
    http = httpx.AsyncClient(base_url="https://mock.test", transport=transport)
    client = PncpClient(base_url="https://mock.test", client=http)

    page = await client.list_contratacoes_por_publicacao(
        data_inicial="2025-04-01",
        data_final="2025-04-02",
        codigo_modalidade=6,
    )
    assert page.empty is True
    assert page.data == []
    await client.aclose()


def _portal_transport(handler):
    return httpx.AsyncClient(base_url="https://mockportal.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_itens_maps_fields_and_handles_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/compras/2024/9/" in str(request.url) or str(request.url).endswith("/compras/2024/9/itens"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            json=[
                {
                    "numeroItem": 1,
                    "descricao": "Recapeamento asfaltico",
                    "temResultado": True,
                    "valorTotal": 1500000.5,
                    "situacaoCompraItemNome": "Homologado",
                },
                {"numeroItem": 2, "descricao": "Sinalizacao", "temResultado": False},
            ],
        )

    client = PncpClient(portal_base_url="https://mockportal.test", portal_client=_portal_transport(handler))
    itens = await client.list_itens(cnpj="00394460000141", ano=2024, sequencial=156)
    assert len(itens) == 2
    assert itens[0].numero_item == 1
    assert itens[0].tem_resultado is True
    assert itens[0].valor_total == 1500000.5
    assert itens[1].tem_resultado is False

    vazio = await client.list_itens(cnpj="00394460000141", ano=2024, sequencial=9)
    assert vazio == []
    await client.aclose()


@pytest.mark.asyncio
async def test_list_item_resultados_maps_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/orgaos/00394460000141/compras/2024/156/itens/1/resultados")
        return httpx.Response(
            200,
            json=[
                {
                    "sequencialResultado": 1,
                    "niFornecedor": "11222333000144",
                    "nomeRazaoSocialFornecedor": "Construtora Alfa LTDA",
                    "valorTotalHomologado": 1450000.0,
                    "valorUnitarioHomologado": 145.0,
                    "quantidadeHomologada": 10000.0,
                    "dataResultado": "2024-11-05",
                    "situacaoCompraItemResultadoNome": "Informado",
                    "porteFornecedorNome": "Demais",
                }
            ],
        )

    client = PncpClient(portal_base_url="https://mockportal.test", portal_client=_portal_transport(handler))
    rows = await client.list_item_resultados(cnpj="00394460000141", ano=2024, sequencial=156, numero_item=1)
    assert len(rows) == 1
    assert rows[0].ni_fornecedor == "11222333000144"
    assert rows[0].valor_total_homologado == 1450000.0
    assert rows[0].sequencial_resultado == 1
    await client.aclose()
