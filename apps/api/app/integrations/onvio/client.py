"""Onvio -- Dominio/Thomson Reuters: envio de NF-e ao contador (Modulo C).

Fluxo (script de referencia da comunidade, a validar com credencial):

1. Token OAuth2 client_credentials em auth.thomsonreuters.com (dura
   ~24h -- cache em memoria com lock, NUNCA em arquivo).
2. `GET  /dominio/integration/v1/activation/info`   -- CNPJs do vinculo
3. `POST /dominio/integration/v1/activation/enable` -- integrationKey
   de sessao (usada nos calls de invoice; cacheada em memoria).
4. `POST /dominio/invoice/v3/batches`      -- multipart file[] + query
5. `GET  /dominio/invoice/v3/batches/{id}` -- sucesso quando
   filesExpanded[0].apiStatus.message == "Arquivo armazenado na API"

Guard-rail: enviar NF-e e ESCRITA no Dominio de producao do escritorio
contabil (sem sandbox conhecido). Envio real exige allow_send=True
(ONVIO_ALLOW_SEND no env); mock nao e afetado.

Nota: `app/integrations/dominio` fala com a Central do Desenvolvedor
(api.dominioexterior.com.br) -- outra superficie para o mesmo objetivo.
Os dois coexistem; a escolha e da camada de servico do fiscal.

Padrao do projeto: sem qualquer uma das 3 credenciais, o adapter cai
num mock deterministico -- mesmo padrao OnSafety/Dominio/OneDrive.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

ONVIO_AUTH_URL = "https://auth.thomsonreuters.com/oauth/token"
ONVIO_API_BASE = "https://api.onvio.com.br"
ONVIO_DEFAULT_AUDIENCE = "409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
_STORED_MESSAGE = "Arquivo armazenado na API"


class OnvioError(RuntimeError):
    """Transporte, status nao-2xx ou payload inesperado do Onvio."""


class OnvioAuthError(OnvioError):
    """Credenciais rejeitadas (token ou integration key)."""


class OnvioSendBlockedError(OnvioError):
    """Envio real de NF-e bloqueado por guard-rail (ONVIO_ALLOW_SEND)."""


class OnvioClient(IntegrationClient):
    name = "onvio"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        integration_key: str | None = None,
        audience: str = ONVIO_DEFAULT_AUDIENCE,
        allow_send: bool = False,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client_id = client_id or ""
        self._client_secret = client_secret or ""
        self._integration_key = integration_key or ""
        self._audience = audience
        self._allow_send = allow_send
        self._own_client = client is None
        # Sem base_url: o client fala com DOIS hosts (auth.thomsonreuters
        # e api.onvio) -- URLs sempre absolutas.
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._activation_key: str | None = None
        self._token_lock = asyncio.Lock()
        self._activation_lock = asyncio.Lock()

    @property
    def is_mock(self) -> bool:
        return not (
            self._client_id and self._client_secret and self._integration_key
        )

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        if self.is_mock:
            return True
        try:
            await self.check_activation()
        except (OnvioError, NotImplementedError):
            return False
        return True

    # ---------------------------- operacoes ------------------------------

    async def check_activation(self) -> dict[str, Any]:
        """CNPJs do vinculo escritorio <-> cliente na ativacao."""
        if self.is_mock:
            return {
                "escritorio_cnpj": "11222333000181",
                "cliente_cnpj": "99888777000162",
                "source": "onvio_mock",
            }
        data = await self._get_json(
            f"{ONVIO_API_BASE}/dominio/integration/v1/activation/info"
        )
        return {
            "escritorio_cnpj": str(
                data.get("accountantOfficeNationalIdentity") or ""
            ),
            "cliente_cnpj": str(data.get("clientNationalIdentity") or ""),
            "source": "onvio",
        }

    # --------------------------- HTTP interno ----------------------------

    async def _get_token(self) -> str:
        # Token dura ~24h; renova com 5 min de folga. Lock evita
        # thundering-herd no /oauth/token (padrao DominioClient).
        async with self._token_lock:
            if self._token and self._token_expires_at - time.time() > 300:
                return self._token
            try:
                r = await self._client.post(
                    ONVIO_AUTH_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "audience": self._audience,
                    },
                )
            except httpx.HTTPError as exc:
                raise OnvioError(f"token Onvio falhou: {exc}") from exc
            if r.status_code in (401, 403):
                raise OnvioAuthError(
                    f"credenciais Onvio rejeitadas ({r.status_code}): "
                    f"{r.text[:200]}"
                )
            if r.status_code != 200:
                raise OnvioError(
                    f"token Onvio {r.status_code}: {r.text[:200]}"
                )
            data = r.json()
            token = data.get("access_token")
            if not token:
                raise OnvioError("resposta /oauth/token sem access_token")
            self._token = str(token)
            self._token_expires_at = time.time() + int(
                data.get("expires_in", 86400)
            )
            return self._token

    async def _api_headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "x-integration-key": self._integration_key,
        }

    async def _get_activation_key(self) -> str:
        # integrationKey de SESSAO devolvida pelo /enable -- usada nos
        # calls de invoice no lugar da key configurada (fluxo do script
        # de referencia; reutilizacao entre envios a confirmar). Lock
        # evita double-POST em /activation/enable sob concorrencia --
        # e escrita real, mesmo padrao de _get_token.
        async with self._activation_lock:
            if self._activation_key:
                return self._activation_key
            data = await self._post_json(
                f"{ONVIO_API_BASE}/dominio/integration/v1/activation/enable"
            )
            key = data.get("integrationKey")
            if not key:
                raise OnvioError(
                    "resposta /activation/enable sem integrationKey"
                )
            self._activation_key = str(key)
            return self._activation_key

    async def _get_json(self, url: str) -> dict[str, Any]:
        headers = await self._api_headers()
        try:
            r = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OnvioError(f"transporte Onvio ({url}): {exc}") from exc
        return self._json_or_raise(r, url)

    async def _post_json(self, url: str) -> dict[str, Any]:
        headers = await self._api_headers()
        try:
            r = await self._client.post(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OnvioError(f"transporte Onvio ({url}): {exc}") from exc
        return self._json_or_raise(r, url)

    def _json_or_raise(self, r: httpx.Response, url: str) -> dict[str, Any]:
        if r.status_code in (401, 403):
            # Token pode ter expirado entre cache e uso -- invalida para
            # o proximo call renovar.
            self._token = None
            self._token_expires_at = 0.0
            raise OnvioAuthError(
                f"Onvio {url} status {r.status_code}: {r.text[:200]}"
            )
        if r.status_code >= 300:
            raise OnvioError(
                f"Onvio {url} status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise OnvioError(
                f"resposta nao-JSON Onvio ({url}): {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise OnvioError(
                f"Onvio {url}: objeto esperado, veio {type(data).__name__}"
            )
        return data

    async def send_nfe_xml(
        self, *, filename: str, content: bytes
    ) -> dict[str, Any]:
        """Envia um XML de NF-e para o Dominio do contador.

        Retorna {batch_id, source}. Envio REAL exige allow_send=True.
        """
        if not content:
            raise ValueError("content vazio")
        if self.is_mock:
            digest = hashlib.sha1(content).hexdigest()[:24]
            logger.info(
                "onvio_mock.send_nfe_xml file=%s bytes=%d", filename,
                len(content),
            )
            return {"batch_id": f"mock-{digest}", "source": "onvio_mock"}
        if not self._allow_send:
            raise OnvioSendBlockedError(
                "envio real de NF-e ao Onvio bloqueado: e escrita no "
                "Dominio de PRODUCAO do escritorio contabil. Se "
                "intencional, setar ONVIO_ALLOW_SEND=true."
            )
        activation_key = await self._get_activation_key()
        token = await self._get_token()
        files = {
            "file[]": (filename, content, "application/xml"),
            "query": (None, '{"boxe/File": false}', "application/json"),
        }
        try:
            r = await self._client.post(
                f"{ONVIO_API_BASE}/dominio/invoice/v3/batches",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-integration-key": activation_key,
                },
                files=files,
            )
        except httpx.HTTPError as exc:
            raise OnvioError(f"envio NF-e Onvio falhou: {exc}") from exc
        data = self._json_or_raise(r, "/dominio/invoice/v3/batches")
        batch_id = data.get("id")
        if not batch_id:
            raise OnvioError(f"resposta de envio sem id: {data}")
        return {"batch_id": str(batch_id), "source": "onvio"}

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """Status de processamento de um envio (batch)."""
        if self.is_mock:
            return {
                "batch_id": batch_id,
                "stored": True,
                "message": _STORED_MESSAGE,
                "source": "onvio_mock",
            }
        activation_key = await self._get_activation_key()
        token = await self._get_token()
        url = f"{ONVIO_API_BASE}/dominio/invoice/v3/batches/{batch_id}"
        try:
            r = await self._client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-integration-key": activation_key,
                },
            )
        except httpx.HTTPError as exc:
            raise OnvioError(f"status de batch Onvio falhou: {exc}") from exc
        data = self._json_or_raise(r, url)
        expanded = data.get("filesExpanded") or []
        message = ""
        if expanded and isinstance(expanded[0], dict):
            message = str(
                (expanded[0].get("apiStatus") or {}).get("message") or ""
            )
        return {
            "batch_id": batch_id,
            "stored": message == _STORED_MESSAGE,
            "message": message,
            "source": "onvio",
        }
