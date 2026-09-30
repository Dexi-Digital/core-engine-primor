"""Envio de e-mail pelo Microsoft 365 da Primor (Microsoft Graph).

Substitui o Resend (cortado em 30/09/2026). A Primor ja tem tenant
Microsoft 365 e uma caixa de sistema (`MAIL_SENDER`, ex.:
`sistemas@primorsolucoes.srv.br`); o envio sai dessa caixa usando o
MESMO app do Entra ID do SharePoint (`MS_GRAPH_TENANT_ID/CLIENT_ID/
CLIENT_SECRET`), via client_credentials.

Permissao necessaria (NAO concedida ainda -- pendencia do TI da Primor):
role RBAC do Exchange "Application Mail.Send" atribuida ao app com
escopo restrito a caixa de sistema (RBAC for Applications). NAO usar a
permissao `Mail.Send` de aplicacao no Entra: ela vale para TODAS as
caixas do tenant. Enquanto a role nao existir, o Graph responde 403 e
este client levanta `GraphMailPermissaoNegada` com mensagem explicita.

E-mail aqui e canal ADICIONAL: os alertas sempre viram notificacao na
plataforma (`app.modules.notificacoes.alertas`). Por isso nao existe
mock que finge enviar -- sem configuracao, `build_mail_client` devolve
None e ninguem tenta mandar nada.

Endpoint: POST https://graph.microsoft.com/v1.0/users/{sender}/sendMail
(202 sem corpo; o Graph nao devolve id da mensagem).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from app.integrations.base import IntegrationClient

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

# Mensagem exibida quando o Graph recusa por permissao. Fica aqui (e nao
# espalhada pelos modulos) para o texto do painel/log ser um so.
MSG_PERMISSAO_PENDENTE = (
    "Microsoft Graph recusou o envio (403): o app do Entra ainda nao tem a "
    "role RBAC do Exchange 'Application Mail.Send' com escopo na caixa "
    "{sender}. Pendencia do TI da Primor -- o alerta segue valendo como "
    "notificacao na plataforma."
)


class GraphMailError(RuntimeError):
    """Falha de envio (auth, rede, payload recusado)."""


class GraphMailPermissaoNegada(GraphMailError):
    """403 do Graph: permissao Mail.Send ainda nao concedida na caixa."""


class GraphMailClient(IntegrationClient):
    name = "msgraph_mail"

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        sender: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not (tenant_id and client_id and client_secret and sender):
            raise ValueError(
                "GraphMailClient exige tenant_id, client_id, client_secret e sender"
            )
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._sender = sender
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        # Mesmo esquema de cache do OneDriveClient (client_credentials,
        # renova 5min antes do expiry). Duplicado de proposito: sao
        # poucas linhas e acoplar os dois adapters obrigaria o envio de
        # e-mail a depender do storage.
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def sender(self) -> str:
        return self._sender

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health_check(self) -> bool:
        # Nao ha endpoint barato que prove Mail.Send sem enviar um e-mail;
        # validamos so que o token sai. A permissao real so aparece no
        # primeiro envio (403 -> GraphMailPermissaoNegada).
        try:
            await self._get_token()
        except GraphMailError:
            return False
        return True

    async def _get_token(self) -> str:
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
                raise GraphMailError(f"falha ao obter token Graph: {exc}") from exc
            if r.status_code != 200:
                raise GraphMailError(
                    f"oauth2 v2.0/token falhou ({r.status_code}): {r.text[:200]}"
                )
            payload = r.json()
            token = payload.get("access_token")
            if not token:
                raise GraphMailError("oauth2 retornou sem access_token")
            expires_in = int(payload.get("expires_in", 3600))
            self._token = token
            self._token_expires_at = now + max(60, expires_in - 300)
            return token

    async def send_mail(
        self,
        *,
        to: Sequence[str],
        subject: str,
        html: str,
    ) -> None:
        """Envia um e-mail HTML a partir da caixa `sender`.

        Raises:
            GraphMailPermissaoNegada: 403 (role Mail.Send nao concedida).
            GraphMailError: qualquer outra falha.
        """
        destinatarios = [e.strip() for e in to if e and e.strip()]
        if not destinatarios:
            raise ValueError("`to` precisa de pelo menos um destinatario")

        body: dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html},
                "toRecipients": [
                    {"emailAddress": {"address": e}} for e in destinatarios
                ],
            },
            "saveToSentItems": True,
        }
        url = f"{GRAPH_BASE}/users/{quote(self._sender, safe='@')}/sendMail"
        token = await self._get_token()
        try:
            r = await self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise GraphMailError(f"sendMail Graph falhou: {exc}") from exc

        if r.status_code == 202:
            return
        if r.status_code == 403:
            raise GraphMailPermissaoNegada(
                MSG_PERMISSAO_PENDENTE.format(sender=self._sender)
            )
        raise GraphMailError(
            f"sendMail Graph retornou {r.status_code}: {r.text[:300]}"
        )


def mail_configurado(settings: Settings) -> bool:
    """Envio so e tentado com credencial Graph + caixa remetente."""
    return bool(
        settings.ms_graph_tenant_id
        and settings.ms_graph_client_id
        and settings.ms_graph_client_secret
        and settings.mail_sender
    )


def build_mail_client(settings: Settings) -> GraphMailClient | None:
    """Cliente real, ou None quando nao configurado (sem mock)."""
    if not mail_configurado(settings):
        return None
    return GraphMailClient(
        tenant_id=settings.ms_graph_tenant_id or "",
        client_id=settings.ms_graph_client_id or "",
        client_secret=settings.ms_graph_client_secret or "",
        sender=settings.mail_sender or "",
    )


@asynccontextmanager
async def abrir_mail_client(
    settings: Settings | None = None,
) -> AsyncIterator[GraphMailClient | None]:
    """Uso: `async with abrir_mail_client() as mailer:` -- fecha o httpx."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    client = build_mail_client(settings)
    try:
        yield client
    finally:
        if client is not None:
            await client.aclose()
