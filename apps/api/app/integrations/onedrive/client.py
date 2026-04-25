"""Adapter para Microsoft Graph (OneDrive / SharePoint).

Decisao de design:
  - OAuth2 client_credentials flow (app-only, sem usuario interativo).
    Token cachado em memoria com TTL razoavel (5min antes do expiry
    real do token retornado pelo /oauth2/v2.0/token).
  - Upload via PUT /drives/{drive}/items/root:/{path}:/content para
    arquivos pequenos (<4MB). Para arquivos maiores, fragmentamos
    via createUploadSession (suporta ate ~250GB).
  - `OneDriveMockClient` retorna URLs e bytes deterministicos baseados
    no path/filename -- igual ao DirectData/LLM mocks. Permite rodar
    testes e UI completos sem credenciais Microsoft.

Endpoints chave do Graph (`https://graph.microsoft.com/v1.0`):
  - PUT  /drives/{drive_id}/items/root:/{path}:/content
  - GET  /drives/{drive_id}/items/{item_id}/content
  - DELETE /drives/{drive_id}/items/{item_id}
  - GET  /drives/{drive_id}/items/{item_id}                 (metadata)
  - POST /drives/{drive_id}/items/root:/{path}:/createUploadSession
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
# 4 MiB e o limite oficial para upload simples; acima disso a Microsoft
# recomenda createUploadSession.
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
# Ao fragmentar, mandamos chunks de 5 MiB -- precisa ser multiplo de 320KiB
# (1024 * 320 = 327680). 5 MiB ~ 16 * 327680 = 5.24 MiB -> arredonda pra 4.92 MiB.
UPLOAD_CHUNK_SIZE = 320 * 1024 * 16  # 5,242,880 bytes


class OneDriveError(RuntimeError):
    """Falha generica do adapter (auth, upload, download)."""


class OneDriveItemNotFound(OneDriveError):
    """O item nao existe no drive (404)."""


class OneDriveClient(IntegrationClient):
    """Cliente real do Microsoft Graph.

    `health_check` valida que conseguimos pegar token e listar a raiz
    do drive configurado; nao escreve nada.
    """

    name = "onedrive"

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        drive_id: str,
        root_folder: str = "MotorCentral/editais",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not (tenant_id and client_id and client_secret and drive_id):
            raise ValueError(
                "OneDriveClient exige tenant_id, client_id, client_secret e drive_id"
            )
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._drive_id = drive_id
        self._root_folder = root_folder.strip("/")
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def root_folder(self) -> str:
        return self._root_folder

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health_check(self) -> bool:
        try:
            await self._auth_header()
        except OneDriveError:
            return False
        # Listar a raiz e barato e prova que o drive_id e valido.
        try:
            r = await self._client.get(
                f"{GRAPH_BASE}/drives/{self._drive_id}/root",
                headers=await self._auth_header(),
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # -- token management -------------------------------------------------

    async def _auth_header(self) -> dict[str, str]:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def _get_token(self) -> str:
        # Lock evita varias coroutines disparando renovacao simultanea.
        async with self._token_lock:
            now = time.time()
            if self._token and now < self._token_expires_at:
                return self._token
            url = TOKEN_URL_TEMPLATE.format(tenant=self._tenant_id)
            data = {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": DEFAULT_SCOPE,
            }
            try:
                r = await self._client.post(url, data=data)
            except httpx.HTTPError as exc:
                raise OneDriveError(f"falha ao obter token Graph: {exc}") from exc
            if r.status_code != 200:
                raise OneDriveError(
                    f"oauth2 v2.0/token falhou ({r.status_code}): {r.text[:200]}"
                )
            payload = r.json()
            token = payload.get("access_token")
            expires_in = int(payload.get("expires_in", 3600))
            if not token:
                raise OneDriveError("oauth2 retornou sem access_token")
            self._token = token
            # Renova 5min antes do expiry real para tolerar clock skew.
            self._token_expires_at = now + max(60, expires_in - 300)
            return token

    # -- file ops ---------------------------------------------------------

    def _full_path(self, relative: str) -> str:
        rel = relative.strip("/")
        if self._root_folder:
            return f"{self._root_folder}/{rel}"
        return rel

    async def upload(
        self, *, relative_path: str, content: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        """Sobe `content` em `{root_folder}/{relative_path}` no drive.

        Retorna o JSON do item criado (com `id`, `webUrl`, `size`).
        Para inputs pequenos usa PUT direto; acima do limite, usa
        upload session com fragmentos.
        """
        # Drena o iterador em memoria. Pra anexos de edital (~10MB max
        # tipico) e aceitavel; quando precisarmos de objetos > 50MB
        # frequentes, refatoro para streaming session.
        buffer = bytearray()
        async for chunk in content:
            if chunk:
                buffer.extend(chunk)
        size = len(buffer)
        path = self._full_path(relative_path)
        if size <= SIMPLE_UPLOAD_LIMIT:
            return await self._simple_upload(path, bytes(buffer))
        return await self._chunked_upload(path, bytes(buffer))

    async def _simple_upload(self, path: str, data: bytes) -> dict[str, Any]:
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/root:/{path}:/content"
        headers = await self._auth_header()
        headers["Content-Type"] = "application/octet-stream"
        try:
            r = await self._client.put(url, headers=headers, content=data)
        except httpx.HTTPError as exc:
            raise OneDriveError(f"upload Graph falhou: {exc}") from exc
        if r.status_code not in (200, 201):
            raise OneDriveError(
                f"upload retornou {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def _chunked_upload(self, path: str, data: bytes) -> dict[str, Any]:
        session = await self._create_upload_session(path)
        upload_url = session["uploadUrl"]
        total = len(data)
        offset = 0
        last: dict[str, Any] | None = None
        while offset < total:
            end = min(offset + UPLOAD_CHUNK_SIZE, total)
            chunk = data[offset:end]
            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end - 1}/{total}",
            }
            r = await self._client.put(upload_url, headers=headers, content=chunk)
            if r.status_code not in (200, 201, 202):
                raise OneDriveError(
                    f"chunk upload retornou {r.status_code}: {r.text[:200]}"
                )
            if r.status_code in (200, 201):
                last = r.json()
            offset = end
        if last is None:
            raise OneDriveError("upload session terminou sem item final")
        return last

    async def _create_upload_session(self, path: str) -> dict[str, Any]:
        url = (
            f"{GRAPH_BASE}/drives/{self._drive_id}/items/root:/{path}:"
            "/createUploadSession"
        )
        headers = await self._auth_header()
        headers["Content-Type"] = "application/json"
        r = await self._client.post(
            url,
            headers=headers,
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        )
        if r.status_code not in (200, 201):
            raise OneDriveError(
                f"createUploadSession {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def download(self, item_id: str) -> bytes:
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}/content"
        r = await self._client.get(url, headers=await self._auth_header())
        if r.status_code == 404:
            raise OneDriveItemNotFound(f"item {item_id} nao encontrado")
        if r.status_code not in (200, 302):
            raise OneDriveError(f"download {r.status_code}: {r.text[:200]}")
        return r.content

    async def delete(self, item_id: str) -> None:
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}"
        r = await self._client.delete(url, headers=await self._auth_header())
        if r.status_code == 404:
            raise OneDriveItemNotFound(f"item {item_id} nao encontrado")
        if r.status_code not in (200, 204):
            raise OneDriveError(f"delete {r.status_code}: {r.text[:200]}")


class OneDriveMockClient(IntegrationClient):
    """Mock determinístico para desbloquear dev/testes sem Azure AD.

    O `id` retornado e um hash do path -- assim, uploads repetidos do
    mesmo path retornam o mesmo id (idempotencia logica). Bytes do
    download sao recuperados de um dict em memoria mesmo, simulando
    persistencia durante o lifetime do processo.
    """

    name = "onedrive_mock"

    def __init__(self, *, root_folder: str = "MotorCentral/editais") -> None:
        self._root_folder = root_folder.strip("/")
        self._store: dict[str, bytes] = {}

    @property
    def root_folder(self) -> str:
        return self._root_folder

    async def aclose(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    def _full_path(self, relative: str) -> str:
        rel = relative.strip("/")
        if self._root_folder:
            return f"{self._root_folder}/{rel}"
        return rel

    @staticmethod
    def _id_for(path: str) -> str:
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()
        # Formato fake-Graph (varia em comprimento mas e estavel).
        return f"mock-{digest[:24]}"

    async def upload(
        self, *, relative_path: str, content: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        buffer = bytearray()
        async for chunk in content:
            if chunk:
                buffer.extend(chunk)
        path = self._full_path(relative_path)
        item_id = self._id_for(path)
        self._store[item_id] = bytes(buffer)
        return {
            "id": item_id,
            "name": relative_path.rsplit("/", 1)[-1],
            "size": len(buffer),
            "webUrl": f"https://onedrive.mock/items/{item_id}",
            "@microsoft.graph.downloadUrl": f"https://onedrive.mock/download/{item_id}",
            "source": "onedrive_mock",
        }

    async def download(self, item_id: str) -> bytes:
        if item_id not in self._store:
            raise OneDriveItemNotFound(f"item {item_id} nao encontrado (mock)")
        return self._store[item_id]

    async def delete(self, item_id: str) -> None:
        if item_id not in self._store:
            raise OneDriveItemNotFound(f"item {item_id} nao encontrado (mock)")
        del self._store[item_id]


def build_onedrive_client(
    *,
    tenant_id: str | None,
    client_id: str | None,
    client_secret: str | None,
    drive_id: str | None,
    root_folder: str,
) -> OneDriveClient | OneDriveMockClient:
    """Fabrica unica usada pelo router.

    Se TODAS as 4 credenciais estiverem presentes, retorna o cliente
    real; caso contrario, retorna o mock. Mantemos a decisao centralizada
    aqui para nao espalhar `if api_key is None` por varios lugares.
    """
    if tenant_id and client_id and client_secret and drive_id:
        return OneDriveClient(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            drive_id=drive_id,
            root_folder=root_folder,
        )
    return OneDriveMockClient(root_folder=root_folder)
