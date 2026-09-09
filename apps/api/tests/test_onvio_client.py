"""Testes do adapter Onvio (Dominio/Thomson Reuters -- NF-e)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.onvio.client import (
    ONVIO_DEFAULT_AUDIENCE,
    OnvioAuthError,
    OnvioClient,
    OnvioSendBlockedError,
)

XML = b"<?xml version='1.0'?><NFe><infNFe Id='NFe123'/></NFe>"


@pytest.mark.asyncio
async def test_mock_send_deterministico():
    c1 = OnvioClient()
    c2 = OnvioClient(client_id="", client_secret=None, integration_key=None)
    r1 = await c1.send_nfe_xml(filename="nf.xml", content=XML)
    r2 = await c2.send_nfe_xml(filename="nf.xml", content=XML)
    assert r1 == r2
    assert r1["source"] == "onvio_mock"
    assert r1["batch_id"].startswith("mock-")


@pytest.mark.asyncio
async def test_mock_send_distingue_conteudos():
    c = OnvioClient()
    r1 = await c.send_nfe_xml(filename="a.xml", content=XML)
    r2 = await c.send_nfe_xml(filename="a.xml", content=XML + b"<!-- x -->")
    assert r1["batch_id"] != r2["batch_id"]


@pytest.mark.asyncio
async def test_mock_send_distingue_conteudos_com_mesmo_1kb_inicial():
    # Hash deve considerar o conteudo INTEIRO, nao so o primeiro 1KB --
    # XMLs de NF-e reais compartilham cabecalhos e divergem depois.
    header = b"<?xml version='1.0'?><NFe>" + b"<!-- pad -->" * 100
    assert len(header) > 1024
    c = OnvioClient()
    r1 = await c.send_nfe_xml(filename="a.xml", content=header + b"<final>A</final>")
    r2 = await c.send_nfe_xml(filename="a.xml", content=header + b"<final>B</final>")
    assert r1["batch_id"] != r2["batch_id"]


@pytest.mark.asyncio
async def test_mock_status_sempre_armazenado():
    c = OnvioClient()
    r = await c.send_nfe_xml(filename="nf.xml", content=XML)
    st = await c.get_batch_status(r["batch_id"])
    assert st["stored"] is True
    assert st["message"] == "Arquivo armazenado na API"
    assert st["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_check_activation():
    c = OnvioClient()
    info = await c.check_activation()
    assert len(info["escritorio_cnpj"]) == 14
    assert len(info["cliente_cnpj"]) == 14
    assert info["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_ignora_guard_allow_send():
    # Guard so vale para envio REAL; mock sempre permitido.
    c = OnvioClient(allow_send=False)
    r = await c.send_nfe_xml(filename="nf.xml", content=XML)
    assert r["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_health_check_true():
    assert await OnvioClient().health_check() is True


CREDS = dict(client_id="cid", client_secret="csec", integration_key="ikey")


def _real_onvio(handler, **kwargs):
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport)
    return OnvioClient(client=http, **{**CREDS, **kwargs})


def _route(request: Request, *, token_calls: list) -> Response | None:
    """Rotas compartilhadas de auth/activation para os handlers."""
    if request.url.host == "auth.thomsonreuters.com":
        token_calls.append(1)
        body = request.content.decode()
        assert "grant_type=client_credentials" in body
        assert "client_id=cid" in body
        assert f"audience={ONVIO_DEFAULT_AUDIENCE}" in body
        return Response(200, json={"access_token": "tok-abc", "expires_in": 86400})
    if request.url.path.endswith("/activation/info"):
        assert request.headers["Authorization"] == "Bearer tok-abc"
        assert request.headers["x-integration-key"] == "ikey"
        return Response(
            200,
            json={
                "accountantOfficeNationalIdentity": "11222333000181",
                "clientNationalIdentity": "99888777000162",
            },
        )
    if request.url.path.endswith("/activation/enable"):
        assert request.headers["x-integration-key"] == "ikey"
        return Response(200, json={"integrationKey": "sess-key-1"})
    return None


@pytest.mark.asyncio
async def test_real_health_check_ok_quando_activation_funciona():
    token_calls: list = []

    def handler(request: Request) -> Response:
        r = _route(request, token_calls=token_calls)
        assert r is not None, f"rota inesperada: {request.url}"
        return r

    c = _real_onvio(handler)
    assert await c.health_check() is True


@pytest.mark.asyncio
async def test_real_health_check_falha_quando_api_down():
    def handler(request: Request) -> Response:
        return Response(500, text="boom")

    c = _real_onvio(handler)
    assert await c.health_check() is False


@pytest.mark.asyncio
async def test_real_check_activation_e_cache_de_token():
    token_calls: list = []

    def handler(request: Request) -> Response:
        r = _route(request, token_calls=token_calls)
        assert r is not None, f"rota inesperada: {request.url}"
        return r

    c = _real_onvio(handler)
    info1 = await c.check_activation()
    info2 = await c.check_activation()
    assert info1["escritorio_cnpj"] == "11222333000181"
    assert info1["cliente_cnpj"] == "99888777000162"
    assert info1["source"] == "onvio"
    assert info2 == info1
    assert len(token_calls) == 1  # token cachado entre chamadas


@pytest.mark.asyncio
async def test_real_token_rejeitado_vira_auth_error():
    def handler(request: Request) -> Response:
        return Response(401, json={"error": "access_denied"})

    c = _real_onvio(handler)
    with pytest.raises(OnvioAuthError):
        await c.check_activation()


@pytest.mark.asyncio
async def test_real_401_em_chamada_autenticada_invalida_activation_key():
    # Um 401 numa chamada ja autenticada (nao so no /oauth/token) deve
    # invalidar token E activation key -- a proxima chamada precisa
    # refazer todo o fluxo (token + /activation/enable), nao so o token.
    token_calls: list = []
    enable_calls: list = []
    status_calls: list = []

    def handler(request: Request) -> Response:
        if request.url.host == "auth.thomsonreuters.com":
            token_calls.append(1)
            return Response(200, json={"access_token": "tok-abc", "expires_in": 86400})
        if request.url.path.endswith("/activation/enable"):
            enable_calls.append(1)
            return Response(200, json={"integrationKey": "sess-key-1"})
        if request.url.path == "/dominio/invoice/v3/batches/batch-77":
            status_calls.append(1)
            if len(status_calls) == 1:
                return Response(401, json={"error": "expired"})
            return Response(
                200,
                json={"filesExpanded": [{"apiStatus": {"message": "Arquivo armazenado na API"}}]},
            )
        raise AssertionError(f"rota inesperada: {request.url}")

    c = _real_onvio(handler, allow_send=True)
    with pytest.raises(OnvioAuthError):
        await c.get_batch_status("batch-77")
    assert len(token_calls) == 1
    assert len(enable_calls) == 1

    st = await c.get_batch_status("batch-77")
    assert st["stored"] is True
    # apos o 401, o cache foi limpo -- a chamada seguinte refaz token
    # e activation key do zero.
    assert len(token_calls) == 2
    assert len(enable_calls) == 2


@pytest.mark.asyncio
async def test_real_send_bloqueado_sem_allow_send():
    def handler(request: Request) -> Response:
        raise AssertionError("nenhuma chamada HTTP deveria acontecer")

    c = _real_onvio(handler, allow_send=False)
    with pytest.raises(OnvioSendBlockedError):
        await c.send_nfe_xml(filename="nf.xml", content=XML)


@pytest.mark.asyncio
async def test_real_send_e_status():
    token_calls: list = []

    def handler(request: Request) -> Response:
        shared = _route(request, token_calls=token_calls)
        if shared is not None:
            return shared
        if request.url.path == "/dominio/invoice/v3/batches":
            # invoice usa a integrationKey de SESSAO do /enable
            assert request.headers["x-integration-key"] == "sess-key-1"
            assert request.method == "POST"
            assert b"NFe123" in request.content
            return Response(200, json={"id": "batch-77"})
        if request.url.path == "/dominio/invoice/v3/batches/batch-77":
            return Response(
                200,
                json={"filesExpanded": [{"apiStatus": {"message": "Arquivo armazenado na API"}}]},
            )
        raise AssertionError(f"rota inesperada: {request.url}")

    c = _real_onvio(handler, allow_send=True)
    sent = await c.send_nfe_xml(filename="nf.xml", content=XML)
    assert sent == {"batch_id": "batch-77", "source": "onvio"}
    st = await c.get_batch_status("batch-77")
    assert st["stored"] is True
    assert st["message"] == "Arquivo armazenado na API"
    assert st["source"] == "onvio"


@pytest.mark.asyncio
async def test_real_status_nao_armazenado():
    token_calls: list = []

    def handler(request: Request) -> Response:
        shared = _route(request, token_calls=token_calls)
        if shared is not None:
            return shared
        return Response(
            200,
            json={"filesExpanded": [{"apiStatus": {"message": "Arquivo com schema invalido"}}]},
        )

    c = _real_onvio(handler, allow_send=True)
    st = await c.get_batch_status("batch-99")
    assert st["stored"] is False
    assert st["message"] == "Arquivo com schema invalido"


@pytest.mark.asyncio
async def test_campo_query_usa_boxeFile_e_nao_boxe_barra_File():
    """A documentacao (solucao 8476) especifica a chave `boxeFile`. O
    codigo tinha `boxe/File`, com uma barra no meio.

    Nunca apareceu porque o envio real nunca rodou -- o guard
    ONVIO_ALLOW_SEND fica off por default. Com credencial real em maos
    (03/09/2026), isso passa a importar.
    """
    enviado: dict = {}

    def handler(request: Request) -> Response:
        url = str(request.url)
        if "oauth/token" in url:
            return Response(200, json={"access_token": "t", "expires_in": 86400})
        if "activation/enable" in url:
            return Response(200, json={"integrationKey": "ik-1"})
        enviado["body"] = request.content.decode("utf-8", "replace")
        return Response(200, json={"id": "batch-1"})

    c = OnvioClient(
        client_id="cid",
        client_secret="csec",
        integration_key="chave-do-contador",
        allow_send=True,
        client=AsyncClient(transport=MockTransport(handler)),
    )
    try:
        await c.send_nfe_xml(filename="nota.xml", content=b"<nfe/>")
    finally:
        await c.aclose()

    assert '"boxeFile"' in enviado["body"], enviado["body"][:300]
    assert "boxe/File" not in enviado["body"]
