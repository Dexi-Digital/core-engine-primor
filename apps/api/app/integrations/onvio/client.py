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
        except OnvioError:
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
        raise NotImplementedError  # Task 5

    async def send_nfe_xml(
        self, *, filename: str, content: bytes
    ) -> dict[str, Any]:
        """Envia um XML de NF-e para o Dominio do contador.

        Retorna {batch_id, source}. Envio REAL exige allow_send=True.
        """
        if not content:
            raise ValueError("content vazio")
        if self.is_mock:
            digest = hashlib.sha1(content[:1024]).hexdigest()[:24]
            logger.info(
                "onvio_mock.send_nfe_xml file=%s bytes=%d", filename,
                len(content),
            )
            return {"batch_id": f"mock-{digest}", "source": "onvio_mock"}
        raise NotImplementedError  # Task 6

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """Status de processamento de um envio (batch)."""
        if self.is_mock:
            return {
                "batch_id": batch_id,
                "stored": True,
                "message": _STORED_MESSAGE,
                "source": "onvio_mock",
            }
        raise NotImplementedError  # Task 6
