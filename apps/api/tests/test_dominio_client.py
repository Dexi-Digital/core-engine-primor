"""Testes do adapter Domínio (real client com httpx.MockTransport + mock)."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.dominio.client import (
    DominioAuthError,
    DominioClient,
    DominioError,
    DominioMockClient,
)


def _make_client(handler) -> DominioClient:
    return DominioClient(
        audit_url="https://test.dominio.com.br",
        integracao="test-integracao",
        client_id="cid",
        client_secret="csecret",
        base_url="https://api.test.local/v1",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_get_token_e_cacheado_entre_chamadas():
    """Regressao: o /token e rate-limitado pela Domínio. Tokens validos
    nao podem ser re-pedidos a cada upload."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "abc", "expires_in": 3600}
            )
        return httpx.Response(
            200, json={"protocolo": "P-1", "status": "ok", "mensagem": ""}
        )

    client = _make_client(handler)
    try:
        # Duas requests consecutivas usam o mesmo token cached.
        await client.upload_xml(filename="a.xml", content=b"<x/>", tipo="nfe")
        await client.upload_xml(filename="b.xml", content=b"<x/>", tipo="nfe")
        token_calls = [c for c in calls if "/token" in c]
        assert len(token_calls) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_upload_retorna_protocolo():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        return httpx.Response(
            200,
            json={
                "protocolo": "DOM-2024-0001",
                "status": "recebido",
                "mensagem": "ok",
            },
        )

    client = _make_client(handler)
    try:
        result = await client.upload_xml(
            filename="nf.xml", content=b"<NFe/>", tipo="nfe"
        )
        assert result["protocolo"] == "DOM-2024-0001"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_credenciais_invalidas_levantam_auth_error():
    """401 no /token vira `DominioAuthError`, nao `DominioError` -- a
    camada de servico distingue para nao retentar (auth nao e
    transitorio, precisa rotar credenciais)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid_client")

    client = _make_client(handler)
    try:
        with pytest.raises(DominioAuthError):
            await client.upload_xml(
                filename="x.xml", content=b"<x/>", tipo="nfe"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_token_expirado_durante_upload_invalida_cache():
    """Se o /upload retornar 401, o cache de token e invalidado para
    proxima chamada nao reusar o token rejeitado."""
    calls: dict[str, int] = {"token": 0, "upload": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/token" in str(request.url):
            calls["token"] += 1
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        calls["upload"] += 1
        return httpx.Response(401, text="expired")

    client = _make_client(handler)
    try:
        with pytest.raises(DominioAuthError):
            await client.upload_xml(
                filename="x.xml", content=b"<x/>", tipo="nfe"
            )
        # cache invalidado -> nova request chamaria /token de novo
        assert client._token is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_timeout_do_httpx_e_envolvido_em_dominio_error():
    """Regressao do contrato: httpx.HTTPError NUNCA pode escapar do
    adapter como excecao crua -- tem que virar DominioError para a
    camada de servico/worker conseguir capturar e atualizar o
    `status_envio`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        raise httpx.ReadTimeout("timeout", request=request)

    client = _make_client(handler)
    try:
        with pytest.raises(DominioError):
            await client.upload_xml(
                filename="x.xml", content=b"<x/>", tipo="nfe"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_token_endpoint_timeout_vira_dominio_error():
    """Mesma regressao no /token -- DNS/timeout no auth precisa virar
    DominioError, nao httpx.ConnectError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS", request=request)

    client = _make_client(handler)
    try:
        with pytest.raises(DominioError):
            await client._get_token()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_resposta_nao_json_no_upload_vira_dominio_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        # 200 OK mas corpo nao-JSON (acidente raro mas possivel se
        # bater num gateway de manutencao).
        return httpx.Response(200, text="<html>maintenance</html>")

    client = _make_client(handler)
    try:
        with pytest.raises(DominioError, match="JSON"):
            await client.upload_xml(
                filename="x.xml", content=b"<x/>", tipo="nfe"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mock_client_retorna_protocolo_deterministico():
    """O mock precisa retornar protocolos deterministicos para que
    testes possam fazer assert sem flakiness. Mas tambem precisa
    distinguir uploads diferentes (nao retornar sempre o mesmo)."""
    mock = DominioMockClient()
    a1 = await mock.upload_xml(filename="a.xml", content=b"AAA", tipo="nfe")
    a2 = await mock.upload_xml(filename="a.xml", content=b"AAA", tipo="nfe")
    b1 = await mock.upload_xml(filename="b.xml", content=b"BBB", tipo="cte")

    # Mesmo conteudo -> mesmo prefixo (digest), mas counter incrementa.
    assert a1["protocolo"].startswith("MOCK-NFE-")
    assert a2["protocolo"].startswith("MOCK-NFE-")
    assert a1["protocolo"] != a2["protocolo"]
    assert b1["protocolo"].startswith("MOCK-CTE-")
    assert b1["protocolo"] != a1["protocolo"]
    assert a1["status"] == "recebido"


@pytest.mark.asyncio
async def test_mock_client_health_check_sempre_ok():
    mock = DominioMockClient()
    assert await mock.health_check() is True


@pytest.mark.asyncio
async def test_token_lock_evita_thundering_herd_em_uploads_concorrentes():
    # Regressao Devin Review #12: o DominioClient agora e singleton de
    # processo (`get_dominio_singleton`). Sem `asyncio.Lock` no
    # `_get_token`, varios uploads simultaneos com cache vazio
    # disparariam N POST /token concorrentes -- a Dominio rate-limita
    # /token. O lock garante que so 1 chamada e feita; as outras
    # esperam e reusam o token cached.
    import asyncio

    token_calls = 0

    async def slow_token_handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if "/token" in str(request.url):
            token_calls += 1
            # Latencia artificial: sem lock, as 5 coroutines passam
            # pelo `if self._token` antes da 1a response chegar e
            # cada uma chama POST /token.
            await asyncio.sleep(0.05)
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        return httpx.Response(
            200, json={"protocolo": "OK", "status": "ok", "mensagem": ""}
        )

    client = _make_client(slow_token_handler)
    try:
        results = await asyncio.gather(
            *[
                client.upload_xml(
                    filename=f"doc{i}.xml", content=b"<x/>", tipo="nfe"
                )
                for i in range(5)
            ]
        )
        assert len(results) == 5
        # 5 uploads concorrentes, mas apenas 1 chamada a /token.
        assert token_calls == 1
    finally:
        await client.aclose()
