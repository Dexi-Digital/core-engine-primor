"""Adapter para Dominio Sistemas - Central do Desenvolvedor.

A API e um servico cloud da Thomson Reuters / Domínio que recebe XMLs
fiscais (NF-e, NFS-e, NFC-e, CT-e, CF-e, Baixa de Parcela) emitidos por
ERPs e os disponibiliza no software contabil Domínio do escritorio
parceiro -- automatiza o "envio para o contador".

Fluxo:
  1. POST /token (audit_url + integracao + client_id + client_secret)
     -> bearer token
  2. POST /upload (multipart/form-data com 'arquivo': bytes do XML)
     -> { protocolo, status, mensagem }

Decisoes de design:
  - `DominioMockClient` retorna protocolos deterministicos (sha1 do xml
    truncado) -- mesmo padrao do OneDrive/DirectData. Permite rodar
    dev/testes sem cadastro de parceiro.
  - Token cachado em memoria com TTL pra evitar bater no /token a cada
    upload -- a Domínio rate-limita login.
  - Erros HTTP de transporte (timeout, DNS) sao envolvidos em
    `DominioError`. A camada de servico/worker captura `DominioError`
    e atualiza `status_envio="erro"` + agenda retry. Sem o wrap, um
    timeout escaparia como httpx.HTTPError e a task crashava sem
    atualizar o registro.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)


# Endpoints da Central do Desenvolvedor. URLs reais sao por ambiente
# (homologacao vs producao), entao ficam parametrizadas.
DEFAULT_BASE_URL = "https://api.dominioexterior.com.br/api/v1"


class DominioError(RuntimeError):
    """Falha generica do adapter (auth, upload, parse)."""


class DominioAuthError(DominioError):
    """Token rejeitado ou credenciais invalidas."""


class DominioClient(IntegrationClient):
    """Cliente real para a API Dominio.

    `audit_url` e `integracao` identificam o escritorio contabil dentro
    do tenant Dominio -- vem da configuracao do parceiro. `client_id`
    e `client_secret` sao gerados na Central do Desenvolvedor.
    """

    name = "dominio"

    def __init__(
        self,
        *,
        audit_url: str,
        integracao: str,
        client_id: str,
        client_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._audit_url = audit_url
        self._integracao = integracao
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health_check(self) -> bool:
        try:
            await self._get_token()
        except DominioError:
            return False
        return True

    async def _get_token(self) -> str:
        # Renova com 5 min de folga antes do expiry. Tokens da Domínio
        # tipicamente duram 1h.
        if self._token and self._token_expires_at - time.time() > 300:
            return self._token
        url = f"{self._base_url}/token"
        try:
            r = await self._client.post(
                url,
                json={
                    "audit_url": self._audit_url,
                    "integracao": self._integracao,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise DominioError(f"token Dominio falhou: {exc}") from exc
        if r.status_code in (401, 403):
            raise DominioAuthError(
                f"credenciais Dominio rejeitadas ({r.status_code}): "
                f"{r.text[:200]}"
            )
        if r.status_code != 200:
            raise DominioError(f"token {r.status_code}: {r.text[:200]}")
        data = r.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise DominioError(f"resposta /token sem access_token: {data}")
        self._token = str(token)
        # `expires_in` em segundos (padrao OAuth2). Se nao vier, assume 1h.
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in
        return self._token

    async def _auth_header(self) -> dict[str, str]:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def upload_xml(
        self, *, filename: str, content: bytes, tipo: str
    ) -> dict[str, Any]:
        """Envia o XML para a Dominio.

        Retorna `{"protocolo": str, "status": str, "mensagem": str}`.
        Levanta `DominioError` em qualquer falha (incluindo HTTP nao-2xx).
        """
        url = f"{self._base_url}/upload"
        headers = await self._auth_header()
        # multipart/form-data: o httpx monta sozinho ao receber `files=`.
        files = {"arquivo": (filename, content, "application/xml")}
        data = {"tipo": tipo}
        try:
            r = await self._client.post(
                url, headers=headers, files=files, data=data
            )
        except httpx.HTTPError as exc:
            raise DominioError(f"upload Dominio falhou: {exc}") from exc
        if r.status_code == 401:
            # Token pode ter expirado entre o cache e o uso -- invalida e
            # deixa caller decidir se retenta. Sem isso, o caller veria
            # DominioError generico e nao saberia que e auth-recoverable.
            self._token = None
            self._token_expires_at = 0.0
            raise DominioAuthError("token rejeitado no upload (401)")
        if r.status_code not in (200, 201):
            raise DominioError(
                f"upload {r.status_code}: {r.text[:300]}"
            )
        try:
            return r.json()
        except ValueError as exc:
            raise DominioError(
                f"resposta /upload nao e JSON valido: {r.text[:200]}"
            ) from exc


class DominioMockClient(IntegrationClient):
    """Mock deterministico para dev/CI sem cadastro de parceiro Dominio.

    Protocolo retornado e o sha1 dos primeiros 1024 bytes do XML +
    timestamp logico mantido em memoria; permite assertions estaveis
    em testes e ainda assim distingue uploads diferentes.
    """

    name = "dominio_mock"

    def __init__(self) -> None:
        self._counter = 0

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def upload_xml(
        self, *, filename: str, content: bytes, tipo: str
    ) -> dict[str, Any]:
        # filename usado so para log; o digest e do conteudo, igual ao
        # protocolo real da Dominio (que e funcao do XML normalizado).
        del filename
        self._counter += 1
        digest = hashlib.sha1(content[:1024]).hexdigest()[:16]
        protocolo = f"MOCK-{tipo.upper()}-{digest}-{self._counter:06d}"
        logger.info(
            "dominio_mock.upload_xml tipo=%s bytes=%d -> %s",
            tipo,
            len(content),
            protocolo,
        )
        return {
            "protocolo": protocolo,
            "status": "recebido",
            "mensagem": "Mock: arquivo aceito e enfileirado para processamento.",
        }
