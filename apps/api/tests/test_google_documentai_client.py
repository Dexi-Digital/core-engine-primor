"""Testes do adapter Google Document AI (Modulo B.2)."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient, MockTransport, Request, Response
from jose import jwt as jose_jwt

from app.integrations.google_documentai.client import (
    DocumentAIAuthError,
    DocumentAIError,
    GoogleDocumentAIClient,
    _parse_float,
    _parse_int,
)

# --- helpers ----------------------------------------------------------------


def _gen_service_account() -> dict[str, str]:
    """Gera uma service-account JSON real (RSA 2048) -- evita usar
    fixture estatica de chave que precisaria ser gitignored."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "type": "service_account",
        "project_id": "test-project",
        "client_email": "ci-bot@test-project.iam.gserviceaccount.com",
        "private_key": pem,
        "private_key_id": "kid-test",
    }


def _real_client(handler, *, sa: dict[str, str] | None = None):
    sa = sa or _gen_service_account()
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport)
    return GoogleDocumentAIClient(
        credentials_json=json.dumps(sa),
        project_id="test-project",
        processor_id="proc-1",
        location="us",
        client=http,
    ), sa


# --- helpers utilitarios ---------------------------------------------------


def test_parse_float_aceita_virgula_e_ponto():
    # Document AI/OCR retorna sempre 1 separador decimal por vez.
    assert _parse_float("1234,56") == 1234.56
    assert _parse_float("1234.56") == 1234.56
    assert _parse_float("texto") is None
    assert _parse_float(None) is None
    assert _parse_float("123") == 123.0


def test_parse_int_extrai_inteiro():
    assert _parse_int("45230") == 45230
    assert _parse_int("KM 12345") == 12345
    assert _parse_int("not a number") is None
    assert _parse_int(None) is None


# --- mock mode (sem credenciais) -------------------------------------------


@pytest.mark.asyncio
async def test_mock_quando_sem_credenciais():
    c = GoogleDocumentAIClient()
    assert c.is_mock is True
    assert await c.health_check() is True


@pytest.mark.asyncio
async def test_mock_quando_credenciais_parciais():
    """Faltando QUALQUER um dos 3 -> mock."""
    c = GoogleDocumentAIClient(
        credentials_json='{"client_email":"x","private_key":"y"}',
        project_id="p",
        # processor_id ausente
    )
    assert c.is_mock is True


@pytest.mark.asyncio
async def test_mock_response_e_deterministico():
    c = GoogleDocumentAIClient()
    r1 = await c.processar_documento(
        content=b"\x25PDF-1.4\n%fake%", mime_type="application/pdf",
        filename="parte-001.pdf",
    )
    r2 = await c.processar_documento(
        content=b"\x25PDF-1.4\n%fake%", mime_type="application/pdf",
        filename="parte-001.pdf",
    )
    assert r1 == r2  # mesmo arquivo -> mesma resposta
    assert r1["source"] == "google_documentai_mock"
    fields = r1["fields"]
    assert fields["data"] is not None
    assert fields["operador"] is not None
    assert fields["horimetro_inicio"] < fields["horimetro_fim"]
    assert fields["km_inicio"] < fields["km_fim"]
    assert "PARTE DIARIA" in r1["raw_text"]


@pytest.mark.asyncio
async def test_mock_diverge_por_filename():
    c = GoogleDocumentAIClient()
    r1 = await c.processar_documento(
        content=b"x", mime_type="image/png", filename="a.png"
    )
    r2 = await c.processar_documento(
        content=b"x", mime_type="image/png", filename="b.png"
    )
    assert r1["fields"] != r2["fields"]


@pytest.mark.asyncio
async def test_mock_creds_json_invalido_cai_em_mock():
    c = GoogleDocumentAIClient(
        credentials_json="<<not json>>",
        project_id="p",
        processor_id="proc-1",
    )
    # JSON malformado: o ctor detecta a falha e LIMPA o _credentials_raw
    # para que `is_mock` devolva True. Sem isso, `processar_documento`
    # levantaria DocumentAIAuthError em produção dando a impressão de
    # que o cliente esta em modo real (o log diz "caindo em modo mock"
    # mas o `is_mock` continuaria False -- bug identificado em code review).
    assert c.is_mock is True
    out = await c.processar_documento(
        content=b"x", mime_type="application/pdf", filename="x.pdf"
    )
    assert out["source"] == "google_documentai_mock"


# --- modo real --------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_client_normaliza_entities():
    """Document AI devolve `document.entities` -- normalizamos para
    o schema canonico de parte_diaria."""

    def handler(request: Request) -> Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return Response(
                200, json={"access_token": "test-token", "expires_in": 3600}
            )
        # processor:process
        assert "processors/proc-1:process" in str(request.url)
        body = json.loads(request.content)
        assert "rawDocument" in body
        return Response(
            200,
            json={
                "document": {
                    "text": "PARTE DIARIA - Joao",
                    "entities": [
                        {"type": "operador", "mentionText": "Joao", "confidence": 0.9},
                        {"type": "obra", "mentionText": "Norte", "confidence": 0.85},
                        {
                            "type": "horimetro_inicio",
                            "mentionText": "1234.5",
                            "confidence": 0.92,
                        },
                        {
                            "type": "km_fim",
                            "mentionText": "45230",
                            "confidence": 0.88,
                        },
                        {
                            "type": "campo_desconhecido",
                            "mentionText": "ignorado",
                            "confidence": 0.5,
                        },
                    ],
                },
            },
        )

    client, _sa = _real_client(handler)
    try:
        r = await client.processar_documento(
            content=b"x", mime_type="application/pdf", filename="x.pdf"
        )
    finally:
        await client.aclose()
    assert r["source"] == "google_documentai"
    assert r["fields"]["operador"] == "Joao"
    assert r["fields"]["obra"] == "Norte"
    assert r["fields"]["horimetro_inicio"] == 1234.5
    assert r["fields"]["km_fim"] == 45230
    assert "campo_desconhecido" not in r["fields"]
    assert r["confidence"] > 0


@pytest.mark.asyncio
async def test_real_client_jwt_assinado_corretamente():
    """O assertion enviado para oauth2.googleapis.com tem que ser um
    JWT RS256 com `iss=client_email` e `aud` correto."""
    captured: dict[str, str] = {}

    def handler(request: Request) -> Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            # assertion vem como form-encoded
            body = request.content.decode()
            for kv in body.split("&"):
                k, _, v = kv.partition("=")
                captured[k] = v
            return Response(
                200, json={"access_token": "ok", "expires_in": 3600}
            )
        return Response(200, json={"document": {"text": "", "entities": []}})

    client, sa = _real_client(handler)
    try:
        await client.processar_documento(
            content=b"x", mime_type="application/pdf"
        )
    finally:
        await client.aclose()

    assert captured["grant_type"].startswith("urn%3Aietf%3Aparams")
    # decode assertion para confirmar claims
    from urllib.parse import unquote

    assertion = unquote(captured["assertion"])
    decoded = jose_jwt.get_unverified_claims(assertion)
    assert decoded["iss"] == sa["client_email"]
    assert decoded["aud"] == "https://oauth2.googleapis.com/token"
    assert decoded["scope"] == "https://www.googleapis.com/auth/cloud-platform"


@pytest.mark.asyncio
async def test_real_client_token_e_cacheado():
    """Mesmo client + 2 chamadas -> 1 unica chamada ao /token."""
    counts = {"token": 0, "process": 0}

    def handler(request: Request) -> Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            counts["token"] += 1
            return Response(
                200, json={"access_token": "tk", "expires_in": 3600}
            )
        counts["process"] += 1
        return Response(
            200, json={"document": {"text": "x", "entities": []}}
        )

    client, _ = _real_client(handler)
    try:
        await client.processar_documento(
            content=b"a", mime_type="application/pdf"
        )
        await client.processar_documento(
            content=b"b", mime_type="application/pdf"
        )
    finally:
        await client.aclose()
    assert counts["token"] == 1
    assert counts["process"] == 2


@pytest.mark.asyncio
async def test_real_client_status_5xx_levanta():
    def handler(request: Request) -> Response:
        if "oauth2.googleapis.com" in str(request.url):
            return Response(200, json={"access_token": "x", "expires_in": 3600})
        return Response(503, text="document ai overloaded")

    client, _ = _real_client(handler)
    try:
        with pytest.raises(DocumentAIError):
            await client.processar_documento(
                content=b"x", mime_type="application/pdf"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_client_status_403_e_auth_error():
    def handler(request: Request) -> Response:
        if "oauth2.googleapis.com" in str(request.url):
            return Response(200, json={"access_token": "x", "expires_in": 3600})
        return Response(403, text="permission denied")

    client, _ = _real_client(handler)
    try:
        with pytest.raises(DocumentAIAuthError):
            await client.processar_documento(
                content=b"x", mime_type="application/pdf"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_client_oauth_falha_levanta_auth():
    def handler(request: Request) -> Response:
        if "oauth2.googleapis.com" in str(request.url):
            return Response(401, text="invalid_grant")
        return Response(200, json={"document": {}})

    client, _ = _real_client(handler)
    try:
        with pytest.raises(DocumentAIAuthError):
            await client.processar_documento(
                content=b"x", mime_type="application/pdf"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_client_token_refresh_serializa_concorrente():
    """Bug identificado em review: 2 OCRs concorrentes batendo o token expirado
    faziam 2 POSTs em /token paralelos (gastando rate-limit Google).

    Com o `asyncio.Lock` em `_get_access_token`, mesmo N coroutines
    iniciadas no mesmo `gather` acabam fazendo 1 unica chamada -- a
    primeira renova, as demais reusam o cache.
    """
    import asyncio

    counts = {"token": 0, "process": 0}
    token_release = asyncio.Event()

    async def slow_handler(request: Request) -> Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            counts["token"] += 1
            # Simula latencia da Google -- garante que segundo `await`
            # da segunda coroutine entra no lock antes do primeiro
            # liberar. Sem o lock o handler eh chamado N vezes
            # (counts['token'] > 1).
            await token_release.wait()
            return Response(
                200, json={"access_token": "tk", "expires_in": 3600}
            )
        counts["process"] += 1
        return Response(
            200, json={"document": {"text": "x", "entities": []}}
        )

    # MockTransport aceita handlers async direto.
    transport = MockTransport(slow_handler)
    http = AsyncClient(transport=transport)
    sa = _gen_service_account()
    client = GoogleDocumentAIClient(
        credentials_json=json.dumps(sa),
        project_id="test-project",
        processor_id="proc-1",
        location="us",
        client=http,
    )
    try:
        # Dispara 5 chamadas concorrentes; sem token cacheado, todas
        # vao para `_get_access_token` simultaneamente.
        async def _call() -> None:
            await client.processar_documento(
                content=b"a", mime_type="application/pdf"
            )

        tasks = [asyncio.create_task(_call()) for _ in range(5)]
        # Da chance pra todas as tasks chegarem no `await token_release`.
        await asyncio.sleep(0.05)
        token_release.set()
        await asyncio.gather(*tasks)
    finally:
        await client.aclose()
    assert counts["token"] == 1, (
        f"esperava 1 chamada /token, foram {counts['token']} "
        "(lock nao serializou refresh concorrente)"
    )
    assert counts["process"] == 5
