"""ViaCEP -- consulta de endereco por CEP.

API publica, gratuita, sem autenticacao. Documentada em https://viacep.com.br.
GET /ws/{cep}/json/  -> 200 com endereco | 200 com {"erro": true} se inexistente.

Usado pelo dossie de admissao para auto-completar endereco do funcionario
a partir do CEP digitado no formulario de cadastro.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

VIACEP_BASE_URL = "https://viacep.com.br"

_CEP_DIGITS = re.compile(r"\D+")


def normalize_cep(cep: str) -> str:
    """Mantem apenas digitos. Retorna string com 8 chars (zero-pad nao feito)."""
    return _CEP_DIGITS.sub("", cep or "")


class ViaCEPError(RuntimeError):
    """Raised on transport errors or HTTP non-2xx responses."""


class ViaCEPNotFoundError(ViaCEPError):
    """CEP existe formalmente (8 digitos) mas nao tem registro no ViaCEP."""


class ViaCEPClient(IntegrationClient):
    name = "viacep"

    def __init__(
        self,
        *,
        base_url: str = VIACEP_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout
        )

    async def health_check(self) -> bool:
        try:
            await self.get_endereco("01310100")  # Av Paulista, conhecido
            return True
        except ViaCEPError:
            return False

    async def get_endereco(self, cep: str) -> dict[str, Any]:
        cep_norm = normalize_cep(cep)
        if len(cep_norm) != 8:
            raise ValueError(f"CEP invalido: {cep!r} (deve ter 8 digitos)")
        try:
            r = await self._client.get(f"/ws/{cep_norm}/json/")
        except httpx.HTTPError as exc:
            raise ViaCEPError(f"erro de transporte ViaCEP: {exc}") from exc
        if r.status_code != 200:
            raise ViaCEPError(
                f"ViaCEP devolveu status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise ViaCEPError(f"resposta nao-JSON do ViaCEP: {exc}") from exc
        # ViaCEP retorna {"erro": true} para CEP nao encontrado.
        if isinstance(data, dict) and data.get("erro"):
            raise ViaCEPNotFoundError(f"CEP {cep_norm} nao encontrado")
        return {
            "cep": data.get("cep", cep_norm),
            "logradouro": data.get("logradouro") or None,
            "bairro": data.get("bairro") or None,
            "cidade": data.get("localidade") or None,
            "uf": data.get("uf") or None,
            "complemento": data.get("complemento") or None,
            "ibge": data.get("ibge") or None,
        }

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
