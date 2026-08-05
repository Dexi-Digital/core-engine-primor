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
        # follow_redirects=True e CRITICO para download: o Graph
        # responde /items/{id}/content com 302 -> URL pre-autenticada
        # do CDN. Sem follow_redirects, .content retorna o body do
        # redirect (vazio) e corrompe silenciosamente todo arquivo
        # baixado, quebrando a analise IA do D.5.
        self._client = httpx.AsyncClient(
            timeout=timeout, transport=transport, follow_redirects=True
        )
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
            try:
                r = await self._client.put(
                    upload_url, headers=headers, content=chunk
                )
            except httpx.HTTPError as exc:
                raise OneDriveError(f"chunk upload Graph falhou: {exc}") from exc
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
        try:
            r = await self._client.post(
                url,
                headers=headers,
                json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
            )
        except httpx.HTTPError as exc:
            raise OneDriveError(
                f"createUploadSession Graph falhou: {exc}"
            ) from exc
        if r.status_code not in (200, 201):
            raise OneDriveError(
                f"createUploadSession {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def download(self, item_id: str) -> bytes:
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}/content"
        try:
            r = await self._client.get(url, headers=await self._auth_header())
        except httpx.HTTPError as exc:
            # Sem este wrap, um timeout/DNS error escaparia como
            # httpx.HTTPError e nao seria capturado por
            # OneDriveStorage.read (so trata OneDriveError) -- a
            # extracao de PDF do D.5 quebraria inteira em uma falha
            # transitoria de rede em um unico anexo.
            raise OneDriveError(f"download Graph falhou: {exc}") from exc
        if r.status_code == 404:
            raise OneDriveItemNotFound(f"item {item_id} nao encontrado")
        # 302 nao e mais aceito aqui porque follow_redirects=True ja
        # resolve o redirect do CDN para nos. Se ainda chegar 302,
        # algo configurou diferente -- nao engolimos silenciosamente.
        if r.status_code != 200:
            raise OneDriveError(f"download {r.status_code}: {r.text[:200]}")
        return r.content

    async def delete(self, item_id: str) -> None:
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}"
        try:
            r = await self._client.delete(
                url, headers=await self._auth_header()
            )
        except httpx.HTTPError as exc:
            raise OneDriveError(f"delete Graph falhou: {exc}") from exc
        if r.status_code == 404:
            raise OneDriveItemNotFound(f"item {item_id} nao encontrado")
        if r.status_code not in (200, 204):
            raise OneDriveError(f"delete {r.status_code}: {r.text[:200]}")

    async def create_folder(self, *, relative_path: str) -> dict[str, Any]:
        """Garante a pasta `{root_folder}/{relative_path}` no drive.

        Idempotente: se ja existe, retorna o item existente. Retorna o
        JSON do item (com `id` e `webUrl`).
        """
        path = self._full_path(relative_path)
        get_url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/root:/{path}"
        try:
            r = await self._client.get(get_url, headers=await self._auth_header())
        except httpx.HTTPError as exc:
            raise OneDriveError(f"get folder Graph falhou: {exc}") from exc
        if r.status_code == 200:
            return r.json()
        if r.status_code != 404:
            raise OneDriveError(
                f"get folder Graph falhou ({r.status_code}): {r.text[:200]}"
            )

        parent, _, name = path.rpartition("/")
        if parent:
            create_url = (
                f"{GRAPH_BASE}/drives/{self._drive_id}/items/root:/{parent}:/children"
            )
        else:
            create_url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/root/children"
        body = {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        }
        try:
            r = await self._client.post(
                create_url, headers=await self._auth_header(), json=body
            )
        except httpx.HTTPError as exc:
            raise OneDriveError(f"create folder Graph falhou: {exc}") from exc
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 409:
            # Corrida com outra criacao: a pasta passou a existir. Rele.
            try:
                r = await self._client.get(
                    get_url, headers=await self._auth_header()
                )
            except httpx.HTTPError as exc:
                raise OneDriveError(f"get folder Graph falhou: {exc}") from exc
            if r.status_code == 200:
                return r.json()
        raise OneDriveError(
            f"create folder Graph falhou ({r.status_code}): {r.text[:200]}"
        )

    async def list_folder(
        self, *, relative_path: str = "", recursive: bool = True
    ) -> list[dict[str, Any]]:
        """Lista arquivos em `{root_folder}/{relative_path}`.

        Retorna 1 dict por arquivo (pasta nao vira item -- so recursao),
        com chaves estaveis:
            id, name, path (relativo ao root_folder), size (int),
            last_modified (str ISO-8601 do Graph, ou None).

        Pagina via `@odata.nextLink` ate o fim. Se `recursive=True`,
        entra em cada subpasta (DFS). Se a pasta raiz nao existir,
        retorna lista vazia (404 tratado como "nada a sincronizar",
        nao erro -- facilita o onboarding em ambientes novos).
        """
        prefix = self._root_folder
        sub = relative_path.strip("/")
        full = f"{prefix}/{sub}" if prefix and sub else (prefix or sub)
        items = await self._list_recursive(full, recursive=recursive)
        # `_list_recursive` devolve paths absolutos a partir da raiz do
        # drive (ex: `MotorCentral/editais/dp/42/NR12.pdf`). Os consumers
        # (parser.parse_path, service.run_sync) esperam paths relativos
        # ao `root_folder` -- o mock ja faz esse strip, manter paridade.
        root = prefix.strip("/")
        if root:
            for item in items:
                p = item["path"]
                if p.startswith(root + "/"):
                    item["path"] = p[len(root) + 1 :]
                elif p == root:
                    item["path"] = ""
        return items

    async def _list_recursive(
        self, path: str, *, recursive: bool
    ) -> list[dict[str, Any]]:
        path = path.strip("/")
        if path:
            url: str | None = (
                f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{path}:/children"
            )
        else:
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/root/children"
        out: list[dict[str, Any]] = []
        while url:
            try:
                r = await self._client.get(
                    url, headers=await self._auth_header()
                )
            except httpx.HTTPError as exc:
                raise OneDriveError(f"list Graph falhou: {exc}") from exc
            if r.status_code == 404:
                return []
            if r.status_code != 200:
                raise OneDriveError(
                    f"list {r.status_code}: {r.text[:200]}"
                )
            payload = r.json()
            for item in payload.get("value", []):
                if "folder" in item:
                    if recursive:
                        sub_path = f"{path}/{item['name']}" if path else item["name"]
                        out.extend(
                            await self._list_recursive(
                                sub_path, recursive=recursive
                            )
                        )
                    continue
                out.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "path": f"{path}/{item['name']}" if path else item["name"],
                        "size": int(item.get("size") or 0),
                        "last_modified": item.get("lastModifiedDateTime"),
                    }
                )
            url = payload.get("@odata.nextLink")
        return out


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
        # Metadata por item_id -- path absoluto (inclui root_folder) +
        # ultimo modificado. Permite `list_folder` devolver dados
        # coerentes sem refletir o dict de bytes.
        self._meta: dict[str, dict[str, Any]] = {}

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
        self._meta[item_id] = {
            "path": path,
            "name": relative_path.rsplit("/", 1)[-1],
            "size": len(buffer),
            "last_modified": "2026-04-23T00:00:00Z",
        }
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
        self._meta.pop(item_id, None)

    async def create_folder(self, *, relative_path: str) -> dict[str, Any]:
        path = self._full_path(relative_path)
        item_id = self._id_for(f"folder:{path}")
        self._meta.setdefault(
            item_id,
            {
                "path": path,
                "name": relative_path.rsplit("/", 1)[-1],
                "size": 0,
                "last_modified": "2026-04-23T00:00:00Z",
                "folder": True,
            },
        )
        return {
            "id": item_id,
            "name": relative_path.rsplit("/", 1)[-1],
            "webUrl": f"https://onedrive.mock/folders/{item_id}",
            "folder": {},
            "source": "onedrive_mock",
        }

    def seed(
        self,
        *,
        relative_path: str,
        content: bytes = b"",
        last_modified: str = "2026-04-23T00:00:00Z",
    ) -> str:
        """Helper para testes: cria um item sem usar async upload().

        Retorna o `item_id` gerado (hash do path, igual ao upload).
        Usado pelos tests de sync pra popular o drive mock com a
        estrutura de pastas esperada (`dp/{id}/{tipo}.pdf`).
        """
        path = self._full_path(relative_path)
        item_id = self._id_for(path)
        self._store[item_id] = content
        self._meta[item_id] = {
            "path": path,
            "name": relative_path.rsplit("/", 1)[-1],
            "size": len(content),
            "last_modified": last_modified,
        }
        return item_id

    async def list_folder(
        self, *, relative_path: str = "", recursive: bool = True
    ) -> list[dict[str, Any]]:
        """Lista items sob `{root_folder}/{relative_path}`.

        Filtra o dict de metadados por prefixo (nao precisa simular
        estrutura de pastas -- uma pasta existe se houver algum item
        com path abaixo dela). `recursive=False` retorna so items
        diretos da pasta (1 nivel); `recursive=True` (default) retorna
        todos os descendentes.
        """
        prefix = self._full_path(relative_path).rstrip("/")
        out: list[dict[str, Any]] = []
        for item_id, meta in self._meta.items():
            if meta.get("folder"):
                # Espelha o cliente real: `_list_recursive` nunca lista uma
                # pasta como item de arquivo (so recursa nela). Sem este
                # filtro, cada `create_folder`/`ensure_project_folder`
                # criaria um "arquivo fantasma" (size 0) nas listagens.
                continue
            path = meta["path"]
            # Pasta em path precisa casar com prefix ate o proximo /.
            if prefix:
                if path == prefix or not path.startswith(prefix + "/"):
                    continue
                rel_to_prefix = path[len(prefix) + 1 :]
            else:
                rel_to_prefix = path
            if not recursive and "/" in rel_to_prefix:
                continue
            # Normaliza: mock expoe path relativo ao `root_folder` pra
            # casar com o comportamento do cliente real.
            root = self._root_folder
            rel_path = (
                path[len(root) + 1 :]
                if root and path.startswith(root + "/")
                else path
            )
            out.append(
                {
                    "id": item_id,
                    "name": meta["name"],
                    "path": rel_path,
                    "size": meta["size"],
                    "last_modified": meta["last_modified"],
                }
            )
        out.sort(key=lambda it: it["path"])
        return out


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
