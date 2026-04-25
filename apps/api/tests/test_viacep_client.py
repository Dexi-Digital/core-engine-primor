"""ViaCEP adapter -- usa MockTransport para nao depender da rede."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.viacep.client import (
    ViaCEPClient,
    ViaCEPError,
    ViaCEPNotFoundError,
)


def _make_client(handler) -> ViaCEPClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://viacep.com.br", transport=transport
    )
    return ViaCEPClient(client=http)


@pytest.mark.asyncio
async def test_get_endereco_normaliza_cep_e_retorna_shape_padrao() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "cep": "01310-100",
                "logradouro": "Avenida Paulista",
                "complemento": "lado impar",
                "bairro": "Bela Vista",
                "localidade": "Sao Paulo",
                "uf": "SP",
                "ibge": "3550308",
            },
        )

    client = _make_client(handler)
    out = await client.get_endereco("01310-100")
    assert "/ws/01310100/json/" in captured["url"]
    assert out["logradouro"] == "Avenida Paulista"
    assert out["cidade"] == "Sao Paulo"
    assert out["uf"] == "SP"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_endereco_raise_not_found_em_payload_erro() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"erro": True})

    client = _make_client(handler)
    with pytest.raises(ViaCEPNotFoundError):
        await client.get_endereco("99999999")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_endereco_rejeita_cep_curto() -> None:
    client = _make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.get_endereco("123")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_endereco_propaga_erro_em_status_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _make_client(handler)
    with pytest.raises(ViaCEPError):
        await client.get_endereco("01310100")
    await client.aclose()
