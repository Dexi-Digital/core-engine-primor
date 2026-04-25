"""DirectData adapter -- modo mock + modo real (com MockTransport)."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.directdata.client import DirectDataClient, DirectDataError


@pytest.mark.asyncio
async def test_modo_mock_quando_sem_chave_retorna_struct_deterministico() -> None:
    """Sem API key, retorna mock estavel para o mesmo CPF."""
    client = DirectDataClient(api_key=None)
    assert client.is_mock is True

    a = await client.consultar_cpf("11144477735")
    b = await client.consultar_cpf("11144477735")
    assert a == b
    assert a["source"] == "directdata_mock"
    assert a["nome"]
    await client.aclose()


@pytest.mark.asyncio
async def test_modo_real_chama_endpoint_e_normaliza() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "nome": "Joao Silva",
                "dataNascimento": "1990-01-15",
                "sexo": "M",
                "nomeMae": "Maria Silva",
                "situacaoCadastral": "REGULAR",
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://apiv3.directd.com.br", transport=transport
    )
    client = DirectDataClient(api_key="real-token", client=http)
    out = await client.consultar_cpf("11144477735")
    assert "real-token" in captured["body"]
    assert out["nome"] == "Joao Silva"
    assert out["situacao_cpf"] == "REGULAR"
    assert out["source"] == "directdata"
    await client.aclose()


@pytest.mark.asyncio
async def test_modo_real_propaga_erro_em_status_500() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(500, text="boom")
    )
    http = httpx.AsyncClient(
        base_url="https://apiv3.directd.com.br", transport=transport
    )
    client = DirectDataClient(api_key="x", client=http)
    with pytest.raises(DirectDataError):
        await client.consultar_cpf("11144477735")
    await client.aclose()


@pytest.mark.asyncio
async def test_rejeita_cpf_curto() -> None:
    client = DirectDataClient(api_key=None)
    with pytest.raises(ValueError):
        await client.consultar_cpf("123")
    await client.aclose()
