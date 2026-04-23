"""ComprasNet (legacy SIASG) edital fallback adapter.

The page
    https://comprasnet.gov.br/ConsultaLicitacoes/download/download_editais_detalhe.asp
is HTML and can be parsed for metadata, but the actual PDF download hits
`/ConsultaLicitacoes/Download/Download.asp?...` behind a javascript
captcha (`ValidaCodigo()`). This adapter is therefore scoped to:

* fetching the edital *metadata* page and parsing its visible links
  (no captcha needed), and
* raising a distinctive `ComprasnetCaptchaRequired` when a caller asks
  for the actual file -- so the service layer can decide to skip the
  fallback or schedule a manual review.

Solving the captcha is out of scope of this PR; future work can plug in
a Playwright-based solver or a human-in-the-loop flow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.integrations.base import IntegrationClient

COMPRASNET_BASE_URL = "https://comprasnet.gov.br"

# Matches download_editais_detalhe.asp relative links that the edital
# summary page exposes (Itens / Grupos / Download). Filenames are
# rendered inside the HTML body as plain text; we keep this parser small
# on purpose -- any schema change on the legacy portal should surface
# via unit tests instead of silent drift.
_DETAIL_LINK_RE = re.compile(
    r'href="(download_editais_detalhe\.asp[^"]*)"', re.IGNORECASE
)


class ComprasnetCaptchaRequired(RuntimeError):
    """Raised when an operation needs the legacy ComprasNet captcha solved."""


@dataclass(slots=True)
class ComprasnetEditalMetadata:
    uasg: str
    modalidade: str
    numero_prp: str
    titulo: str | None
    objeto: str | None


class ComprasnetClient(IntegrationClient):
    """Minimal ComprasNet adapter; see module docstring for limitations."""

    name = "comprasnet"

    def __init__(
        self,
        base_url: str = COMPRASNET_BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"User-Agent": "MotorCentralBot/0.1 (+https://motorcentral.dev)"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            resp = await client.get("/", timeout=10.0)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    async def fetch_edital_detail_html(
        self, *, uasg: str, modalidade: str, numero_prp: str
    ) -> str:
        """Return the raw HTML of the legacy edital detail page."""
        client = await self._get_client()
        path = "/ConsultaLicitacoes/download/download_editais_detalhe.asp"
        params = {"coduasg": uasg, "modprp": modalidade, "numprp": numero_prp}
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        # The legacy page is served in latin1 with no declared charset.
        # httpx defaults to utf-8 which garbles the text; try latin1 first.
        return resp.content.decode("latin-1", errors="replace")

    async def download_edital_pdf(self, *, uasg: str, modalidade: str, numero_prp: str) -> bytes:
        """Would download the actual PDF; currently blocked on captcha."""
        raise ComprasnetCaptchaRequired(
            "ComprasNet direct PDF download requires solving a legacy captcha "
            "rendered by javascript on /ConsultaLicitacoes/Download/Download.asp. "
            "Use the PNCP adapter first; this fallback is metadata-only until "
            "a captcha solver lands."
        )


def extract_edital_links(html: str) -> list[str]:
    """Return all relative edital-detail links embedded in the HTML."""
    return _DETAIL_LINK_RE.findall(html or "")
