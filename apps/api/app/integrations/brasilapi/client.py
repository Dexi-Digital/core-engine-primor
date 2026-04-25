"""BrasilAPI -- agregador publico de dados oficiais brasileiros.

Docs: https://brasilapi.com.br/docs

Usamos so o endpoint de CNPJ (`GET /api/cnpj/v1/{cnpj}`) para o dossie
de admissao -- valida e expande o CNPJ de empregos anteriores informados
pelo funcionario. API publica, gratuita, sem autenticacao.

Outros endpoints uteis (banco, FIPE, feriados) podem ser adicionados depois.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

BRASILAPI_BASE_URL = "https://brasilapi.com.br"

_DIGITS = re.compile(r"\D+")


def normalize_cnpj(cnpj: str) -> str:
    return _DIGITS.sub("", cnpj or "")


class BrasilAPIError(RuntimeError):
    """Erro de transporte ou status nao-2xx do BrasilAPI."""


class BrasilAPINotFoundError(BrasilAPIError):
    """CNPJ formalmente valido mas sem registro na Receita Federal."""


class BrasilAPIClient(IntegrationClient):
    name = "brasilapi"

    def __init__(
        self,
        *,
        base_url: str = BRASILAPI_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout
        )

    async def health_check(self) -> bool:
        try:
            # Banco do Brasil (codigo 1) -- endpoint cheap e estavel.
            r = await self._client.get("/api/banks/v1/1")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def get_cnpj(self, cnpj: str) -> dict[str, Any]:
        cnpj_norm = normalize_cnpj(cnpj)
        if len(cnpj_norm) != 14:
            raise ValueError(f"CNPJ invalido: {cnpj!r} (deve ter 14 digitos)")
        try:
            r = await self._client.get(f"/api/cnpj/v1/{cnpj_norm}")
        except httpx.HTTPError as exc:
            raise BrasilAPIError(f"transporte BrasilAPI: {exc}") from exc
        if r.status_code == 404:
            raise BrasilAPINotFoundError(
                f"CNPJ {cnpj_norm} nao encontrado na Receita Federal"
            )
        if r.status_code != 200:
            raise BrasilAPIError(
                f"BrasilAPI status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise BrasilAPIError(f"resposta nao-JSON: {exc}") from exc

        # Normalizamos para nosso shape padrao -- nao expomos dezenas de
        # campos da Receita que nao usamos.
        return {
            "cnpj": data.get("cnpj") or cnpj_norm,
            "razao_social": data.get("razao_social"),
            "nome_fantasia": data.get("nome_fantasia"),
            "situacao_cadastral": data.get("descricao_situacao_cadastral"),
            "data_inicio_atividade": data.get("data_inicio_atividade"),
            "cnae_principal": data.get("cnae_fiscal_descricao"),
            "logradouro": data.get("logradouro"),
            "numero": data.get("numero"),
            "complemento": data.get("complemento"),
            "bairro": data.get("bairro"),
            "cep": data.get("cep"),
            "uf": data.get("uf"),
            "municipio": data.get("municipio"),
            "telefone": data.get("ddd_telefone_1"),
            "porte": data.get("porte"),
            "natureza_juridica": data.get("natureza_juridica"),
            "capital_social": data.get("capital_social"),
        }

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
