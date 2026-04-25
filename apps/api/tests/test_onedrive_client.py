"""Tests para `OneDriveClient` e `OneDriveMockClient`.

Para o cliente real usamos `httpx.MockTransport` para encenar
o /oauth2/v2.0/token + endpoints do Graph -- mantemos os testes
totalmente offline (sem rede).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
import pytest

from app.integrations.onedrive.client import (
    SIMPLE_UPLOAD_LIMIT,
    OneDriveClient,
    OneDriveError,
    OneDriveItemNotFound,
    OneDriveMockClient,
    build_onedrive_client,
)


async def _stream(payload: bytes, chunk: int = 8) -> AsyncIterator[bytes]:
    for i in range(0, len(payload), chunk):
        yield payload[i : i + chunk]


def _build_client(handler) -> OneDriveClient:
    transport = httpx.MockTransport(handler)
    return OneDriveClient(
        tenant_id="tenant-uuid",
        client_id="client-uuid",
        client_secret="shhh",
        drive_id="b!fakeDriveId",
        root_folder="MotorCentral/editais",
        transport=transport,
    )


# -- mock client ---------------------------------------------------


@pytest.mark.asyncio
async def test_mock_upload_returns_deterministic_id():
    mock = OneDriveMockClient()
    payload = b"hello world"
    item = await mock.upload(relative_path="42/edital.pdf", content=_stream(payload))
    assert item["id"].startswith("mock-")
    assert item["size"] == len(payload)
    assert item["@microsoft.graph.downloadUrl"].endswith(item["id"])
    assert item["source"] == "onedrive_mock"

    # Same path -> same id (idempotente).
    item2 = await mock.upload(
        relative_path="42/edital.pdf", content=_stream(b"replaced")
    )
    assert item2["id"] == item["id"]


@pytest.mark.asyncio
async def test_mock_download_returns_stored_bytes():
    mock = OneDriveMockClient()
    payload = b"some bytes"
    item = await mock.upload(relative_path="x/y.pdf", content=_stream(payload))
    got = await mock.download(item["id"])
    assert got == payload


@pytest.mark.asyncio
async def test_mock_download_unknown_raises_item_not_found():
    mock = OneDriveMockClient()
    with pytest.raises(OneDriveItemNotFound):
        await mock.download("mock-does-not-exist")


@pytest.mark.asyncio
async def test_mock_delete_then_download_404():
    mock = OneDriveMockClient()
    item = await mock.upload(relative_path="d/del.pdf", content=_stream(b"a"))
    await mock.delete(item["id"])
    with pytest.raises(OneDriveItemNotFound):
        await mock.download(item["id"])


@pytest.mark.asyncio
async def test_mock_delete_unknown_raises():
    mock = OneDriveMockClient()
    with pytest.raises(OneDriveItemNotFound):
        await mock.delete("mock-nope")


# -- factory -------------------------------------------------------


def test_factory_returns_mock_when_credentials_missing():
    client = build_onedrive_client(
        tenant_id=None,
        client_id="x",
        client_secret="y",
        drive_id="z",
        root_folder="r",
    )
    assert isinstance(client, OneDriveMockClient)


def test_factory_returns_real_client_when_all_credentials_present():
    client = build_onedrive_client(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        drive_id="d",
        root_folder="r",
    )
    assert isinstance(client, OneDriveClient)


# -- real client (httpx mock transport) ---------------------------


@pytest.mark.asyncio
async def test_real_simple_upload_then_download_roundtrip():
    """PUT /content para arquivo pequeno + token cache + GET /content."""

    state = {"token_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            state["token_calls"] += 1
            return httpx.Response(
                200,
                json={"access_token": "TOKEN-ABC", "expires_in": 3600},
            )
        # Upload simples
        if request.method == "PUT" and ":/content" in url:
            assert request.headers.get("Authorization") == "Bearer TOKEN-ABC"
            assert b"hello-world" in request.content
            return httpx.Response(
                201,
                json={
                    "id": "01ABCDEF",
                    "name": "edital.pdf",
                    "size": len(request.content),
                    "webUrl": "https://contoso.sharepoint.com/Doc.aspx",
                },
            )
        # Download
        if request.method == "GET" and url.endswith("/items/01ABCDEF/content"):
            return httpx.Response(200, content=b"hello-world")
        # Health-check root
        if request.method == "GET" and url.endswith("/root"):
            return httpx.Response(200, json={"id": "root", "name": "root"})
        # Delete
        if request.method == "DELETE" and url.endswith("/items/01ABCDEF"):
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected {request.method} {url}")

    client = _build_client(handler)
    try:
        item = await client.upload(
            relative_path="99/edital.pdf", content=_stream(b"hello-world")
        )
        assert item["id"] == "01ABCDEF"
        assert item["size"] == len("hello-world")

        # Token cached (uma unica chamada para token apesar de varios endpoints).
        first_token_calls = state["token_calls"]

        body = await client.download("01ABCDEF")
        assert body == b"hello-world"

        # Health check usa o mesmo token.
        ok = await client.health_check()
        assert ok is True

        await client.delete("01ABCDEF")

        # token nao foi renovado (cache funcionou).
        assert state["token_calls"] == first_token_calls
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_token_failure_propagates_as_onedrive_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/oauth2/v2.0/token" in str(request.url):
            return httpx.Response(401, text="bad client_secret")
        return httpx.Response(500)

    client = _build_client(handler)
    try:
        with pytest.raises(OneDriveError, match="oauth2"):
            await client.upload(
                relative_path="x/y.pdf", content=_stream(b"data")
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_token_renews_after_expiry():
    """Quando expiry passa, _get_token renova; outras requests usam o novo."""

    tokens_issued: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            t = f"TOKEN-{len(tokens_issued)}"
            tokens_issued.append(t)
            # expires_in muito curto force refresh
            return httpx.Response(200, json={"access_token": t, "expires_in": 60})
        if request.method == "GET" and url.endswith("/root"):
            return httpx.Response(200, json={"id": "root"})
        return httpx.Response(404)

    client = _build_client(handler)
    try:
        await client.health_check()
        # Forcamos expiry.
        client._token_expires_at = time.time() - 1  # type: ignore[attr-defined]
        await client.health_check()
        assert len(tokens_issued) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_chunked_upload_for_large_file():
    """Acima de 4MB, usa createUploadSession + PUT incremental."""

    payload = b"X" * (SIMPLE_UPLOAD_LIMIT + 1024)

    upload_session_url = "https://uploads.contoso.com/sessions/abc"
    received: dict[str, int] = {"chunks": 0, "total_bytes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        if (
            request.method == "POST"
            and url.endswith(":/createUploadSession")
        ):
            return httpx.Response(200, json={"uploadUrl": upload_session_url})
        if request.method == "PUT" and url == upload_session_url:
            received["chunks"] += 1
            received["total_bytes"] += len(request.content)
            # Simula 202 ate o ultimo chunk; ai retorna 201 com o item final.
            done = received["total_bytes"] >= len(payload)
            if done:
                return httpx.Response(
                    201,
                    json={
                        "id": "BIG-ID",
                        "name": "huge.pdf",
                        "size": len(payload),
                    },
                )
            return httpx.Response(
                202,
                json={
                    "expirationDateTime": "2099-01-01T00:00:00Z",
                    "nextExpectedRanges": [
                        f"{received['total_bytes']}-{len(payload)-1}"
                    ],
                },
            )
        return httpx.Response(404, text=f"unexpected {request.method} {url}")

    client = _build_client(handler)
    try:
        item = await client.upload(
            relative_path="42/huge.pdf", content=_stream(payload, chunk=4096)
        )
        assert item["id"] == "BIG-ID"
        # Pelo menos um chunk via session (vs. upload simples).
        assert received["chunks"] >= 1
        assert received["total_bytes"] == len(payload)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_download_404_raises_item_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/oauth2/v2.0/token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        if request.method == "GET" and "/items/missing/content" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(500)

    client = _build_client(handler)
    try:
        with pytest.raises(OneDriveItemNotFound):
            await client.download("missing")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_delete_404_is_idempotent_via_storage_layer():
    """Delete 404 propaga como OneDriveItemNotFound -- e a storage layer
    transforma isso em no-op (testado em test_onedrive_storage.py)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/oauth2/v2.0/token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        if request.method == "DELETE":
            return httpx.Response(404)
        return httpx.Response(500)

    client = _build_client(handler)
    try:
        with pytest.raises(OneDriveItemNotFound):
            await client.delete("missing")
    finally:
        await client.aclose()


def test_token_lock_prevents_concurrent_token_calls():
    """Garante que duas coroutines simultaneas nao disparam dois POST /token.

    Importante porque cada token call gera audit no Azure AD e tem
    rate-limit; se cada upload paralelo renovar o token, o numero de
    chamadas explode.
    """
    state = {"token_calls": 0}

    async def slow_token(request: httpx.Request) -> httpx.Response:
        if "/oauth2/v2.0/token" in str(request.url):
            state["token_calls"] += 1
            await asyncio.sleep(0.05)
            return httpx.Response(
                200, json={"access_token": "T", "expires_in": 3600}
            )
        if request.method == "GET" and str(request.url).endswith("/root"):
            return httpx.Response(200, json={"id": "root"})
        return httpx.Response(404)

    async def run() -> None:
        client = _build_client(slow_token)
        try:
            await asyncio.gather(
                client.health_check(),
                client.health_check(),
                client.health_check(),
            )
        finally:
            await client.aclose()

    asyncio.run(run())
    assert state["token_calls"] == 1
