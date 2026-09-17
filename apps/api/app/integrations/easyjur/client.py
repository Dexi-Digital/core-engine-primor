"""Adapter do EasyJur (sistema juridico -- processos e tramitacao).

**Nao e RPA de navegador.** A ideia inicial era dirigir o Chromium,
mas a inspecao do login em 17/09/2026 mostrou algo melhor: o
formulario e interceptado por jQuery e faz `POST /acesso/api/login.php`
com `email`/`password`, devolvendo **JSON estruturado**. Sem captcha e
sem segundo fator.

Entao da para falar HTTP direto, como os demais adapters do projeto:
sem Chromium (+400 MB na imagem), sem serviço separado, sem quebrar a
cada mudanca de layout. Um robo de tela seria pior em todos os eixos.

    POST /acesso/api/login.php   ->  {"status": 200} ou
                                     {"status": 400, "erros": {...}}
    sessao por cookie `PHPSESSID`

**Bloqueio de conta.** O EasyJur bloqueia o login apos 5 tentativas
consecutivas erradas, e informa quantas restam. Este adapter LEVA ISSO
A SERIO: guarda o contador da ultima resposta e se RECUSA a tentar de
novo quando esta perto do limite (`_MARGEM_SEGURANCA`). Melhor falhar
dizendo "nao vou tentar" do que bloquear a conta de quem usa o sistema
para trabalhar.

Descoberto tambem: a pagina carrega `accounts.google.com/gsi/client` e
existe `api/login_google.php`. Se a conta do escritorio entra pelo
botao do Google, nao ha senha para usar aqui -- o caminho seria outro.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

EASYJUR_BASE_URL = "https://app.easyjur.com"
_ENDPOINT_LOGIN = "/acesso/api/login.php"
_PAGINA_LOGIN = "/acesso/login.php"

# Nao chegamos perto do bloqueio: com 1 tentativa restante, paramos.
# O custo de errar aqui recai sobre uma pessoa que precisa do sistema
# para trabalhar, nao sobre nos.
_MARGEM_SEGURANCA = 1


class EasyjurError(RuntimeError):
    """Falha de transporte ou resposta inesperada do EasyJur."""


class EasyjurAuthError(EasyjurError):
    """Credenciais recusadas.

    Separada de `EasyjurError` porque nao e transitoria: retentar com a
    mesma senha so consome tentativa e aproxima o bloqueio.
    """

    def __init__(self, mensagem: str, tentativas_restantes: int | None = None):
        super().__init__(mensagem)
        self.tentativas_restantes = tentativas_restantes


class EasyjurBloqueioIminenteError(EasyjurAuthError):
    """Recusa PREVENTIVA: poucas tentativas restantes.

    Levantada por nos, nao pelo EasyJur -- e o adapter se recusando a
    gastar a ultima tentativa.
    """


class EasyjurClient(IntegrationClient):
    name = "easyjur"

    def __init__(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        base_url: str = EASYJUR_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 45.0,
    ) -> None:
        self._email = email or ""
        self._password = password or ""
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout, follow_redirects=True
        )
        self._autenticado = False
        # Ultimo contador informado pelo EasyJur. None = desconhecido.
        self.tentativas_restantes: int | None = None
        if not self._email or not self._password:
            logger.info(
                "easyjur.mock_mode sem EASYJUR_EMAIL/EASYJUR_PASSWORD -- "
                "adapter nao autentica"
            )

    @property
    def is_mock(self) -> bool:
        return not (self._email and self._password)

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        """Alcanca a pagina de login SEM autenticar.

        De proposito: um health check que faz login gastaria tentativa a
        cada verificacao, e um painel de status que bloqueia a conta do
        cliente seria pior do que nao ter painel.
        """
        if self.is_mock:
            return False
        try:
            r = await self._client.get(_PAGINA_LOGIN)
        except httpx.HTTPError as exc:
            logger.warning("easyjur health_check falhou: %s", exc)
            return False
        return r.status_code == 200

    async def login(self) -> None:
        """Autentica e guarda a sessao no cookie jar do client.

        Idempotente: se ja autenticou nesta instancia, nao repete.
        """
        if self.is_mock:
            raise EasyjurAuthError("EasyJur sem credenciais configuradas")
        if self._autenticado:
            return
        if (
            self.tentativas_restantes is not None
            and self.tentativas_restantes <= _MARGEM_SEGURANCA
        ):
            raise EasyjurBloqueioIminenteError(
                "Recusando tentar: restam "
                f"{self.tentativas_restantes} tentativa(s) antes de o "
                "EasyJur bloquear este login. Corrija a senha antes de "
                "tentar de novo.",
                tentativas_restantes=self.tentativas_restantes,
            )

        # Visita a pagina antes: e o que cria o PHPSESSID que o POST usa.
        try:
            await self._client.get(_PAGINA_LOGIN)
            resp = await self._client.post(
                _ENDPOINT_LOGIN,
                data={"email": self._email, "password": self._password},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self._client.base_url}{_PAGINA_LOGIN}",
                },
            )
        except httpx.HTTPError as exc:
            raise EasyjurError(f"EasyJur login falhou: {exc}") from exc

        try:
            dados: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise EasyjurError(
                f"EasyJur login devolveu resposta nao-JSON "
                f"(status {resp.status_code})"
            ) from exc

        erros = dados.get("erros") or {}
        if erros or dados.get("status") not in (200, None):
            restantes = _extrair_tentativas(erros)
            self.tentativas_restantes = restantes
            mensagem = _limpar_html(
                erros.get("mensagem") or dados.get("mensagem") or ""
            ) or "credenciais recusadas"
            raise EasyjurAuthError(
                f"EasyJur recusou o login: {mensagem}",
                tentativas_restantes=restantes,
            )

        self._autenticado = True
        # Login OK zera a contagem consecutiva no lado deles.
        self.tentativas_restantes = None
        logger.info("easyjur.login_ok email=%s", _mascarar(self._email))


def _extrair_tentativas(erros: dict[str, Any]) -> int | None:
    """Menor contador entre os limites por email e por IP.

    O EasyJur conta os dois separadamente; quem bloqueia primeiro e o
    menor -- e e esse que importa para decidir parar.
    """
    bloco = erros.get("tentativas_restantes")
    if not isinstance(bloco, dict):
        return None
    valores = [v for v in bloco.values() if isinstance(v, int)]
    return min(valores) if valores else None


def _limpar_html(texto: str) -> str:
    """As mensagens deles vem com `<br>`. Uma linha le melhor em log."""
    return " ".join(texto.replace("<br>", " ").split())


def _mascarar(email: str) -> str:
    usuario, _, dominio = email.partition("@")
    return f"{usuario[:2]}***@{dominio}" if dominio else "***"
