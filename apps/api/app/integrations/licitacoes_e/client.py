"""Licitacoes-e (Banco do Brasil) edital fallback adapter.

Portal URL: https://www.licitacoes-e.com.br

The BB portal is a classic heavy-JS ASP.NET application: the search
and detail pages emit viewstate cookies and the download action is
gated behind an identified session (login) for non-public atas. A full
implementation needs Playwright piloting the browser + credentials of
a cadastrado fornecedor.

This adapter is shipped as a scaffold so the service layer can route
fallback requests here without an ImportError. All real operations
raise `LicitacoesECredentialsRequired`; the constructor accepts the
eventual credential parameters so the future Playwright implementation
lands without touching callers.
"""
from __future__ import annotations

from app.integrations.base import IntegrationClient

LICITACOES_E_BASE_URL = "https://www.licitacoes-e.com.br"


class LicitacoesECredentialsRequired(RuntimeError):
    """Raised when the operation needs a logged-in BB session."""


class LicitacoesEClient(IntegrationClient):
    """Scaffold; not runtime-functional in this PR."""

    name = "licitacoes_e"

    def __init__(
        self,
        base_url: str = LICITACOES_E_BASE_URL,
        cpf: str | None = None,
        senha: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cpf = cpf
        self._senha = senha

    async def health_check(self) -> bool:
        return self._cpf is not None and self._senha is not None

    async def download_edital_pdf(self, *, edital_id: str) -> bytes:
        raise LicitacoesECredentialsRequired(
            "Licitacoes-e requires a logged-in BB session (CPF + senha) and a "
            "Playwright browser to reach the edital download action. This "
            "adapter is a scaffold; wire credentials via settings and replace "
            "with a Playwright flow before enabling."
        )
