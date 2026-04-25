"""BrasilAPI adapter -- consulta CNPJ com MockTransport."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.brasilapi.client import (
    BrasilAPIClient,
    BrasilAPIError,
    BrasilAPINotFoundError,
)


def _make_client(handler) -> BrasilAPIClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://brasilapi.com.br", transport=transport
    )
    return BrasilAPIClient(client=http)


SAMPLE_CNPJ_RESPONSE = {
    "cnpj": "00000000000191",
    "razao_social": "BANCO DO BRASIL S.A.",
    "nome_fantasia": "BB",
    "descricao_situacao_cadastral": "ATIVA",
    "data_inicio_atividade": "1966-08-01",
    "cnae_fiscal_descricao": "Bancos comerciais",
    "logradouro": "SAUN QUADRA 5 LOTE B",
    "numero": "S/N",
    "complemento": "TORRES I, II E III",
    "bairro": "ASA NORTE",
    "cep": "70040912",
    "uf": "DF",
    "municipio": "BRASILIA",
    "ddd_telefone_1": "61 34939002",
    "porte": "DEMAIS",
    "natureza_juridica": "2038",
    "capital_social": 90000000000.0,
}


@pytest.mark.asyncio
async def test_get_cnpj_normaliza_e_extrai_subset() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=SAMPLE_CNPJ_RESPONSE)

    client = _make_client(handler)
    out = await client.get_cnpj("00.000.000/0001-91")
    assert "/api/cnpj/v1/00000000000191" in captured["url"]
    assert out["razao_social"] == "BANCO DO BRASIL S.A."
    assert out["uf"] == "DF"
    assert out["situacao_cadastral"] == "ATIVA"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_cnpj_404_vira_not_found_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    client = _make_client(handler)
    with pytest.raises(BrasilAPINotFoundError):
        await client.get_cnpj("00000000000191")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_cnpj_rejeita_tamanho_invalido() -> None:
    client = _make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.get_cnpj("123")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_cnpj_propaga_em_status_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _make_client(handler)
    with pytest.raises(BrasilAPIError):
        await client.get_cnpj("00000000000191")
    await client.aclose()
