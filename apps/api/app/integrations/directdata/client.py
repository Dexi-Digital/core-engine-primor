"""DirectData -- enriquecimento de CPF (paga).

Scaffold pronto para quando a Primor contratar o plano. Hoje opera em
modo *mock* (retorna struct deterministico baseado no CPF) sempre que
`DIRECTDATA_API_KEY` esta vazio. Quando a chave estiver configurada,
ele bate o endpoint real -- a forma da resposta normalizada permanece
a mesma para a UI e o service nao mudarem.

Docs (publicas): https://directd.com.br/api
Endpoint usado: POST {base}/api/v1/consultas/cadastro_pessoa
Body: {"token": "<api_key>", "cpf": "<cpf>"}

Mantemos os adapters do dossie todos com o mesmo formato (`name`,
`health_check`, `aclose`) para o painel de status agregado funcionar.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

DIRECTDATA_BASE_URL = "https://apiv3.directd.com.br"

_DIGITS = re.compile(r"\D+")


def normalize_cpf(cpf: str) -> str:
    return _DIGITS.sub("", cpf or "")


class DirectDataError(RuntimeError):
    """Transporte ou status nao-2xx do DirectData."""


class DirectDataClient(IntegrationClient):
    name = "directdata"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DIRECTDATA_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 12.0,
    ) -> None:
        self._api_key = api_key or ""
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout
        )

    @property
    def is_mock(self) -> bool:
        return not self._api_key

    async def health_check(self) -> bool:
        # Sem chave: marcamos como "saudavel" pois o mock sempre responde.
        return True

    async def consultar_cpf(self, cpf: str) -> dict[str, Any]:
        cpf_norm = normalize_cpf(cpf)
        if len(cpf_norm) != 11:
            raise ValueError(f"CPF invalido: {cpf!r} (deve ter 11 digitos)")

        if self.is_mock:
            return self._mock_response(cpf_norm)

        try:
            r = await self._client.post(
                "/api/v1/consultas/cadastro_pessoa",
                json={"token": self._api_key, "cpf": cpf_norm},
            )
        except httpx.HTTPError as exc:
            raise DirectDataError(f"transporte DirectData: {exc}") from exc
        if r.status_code != 200:
            raise DirectDataError(
                f"DirectData status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise DirectDataError(f"resposta nao-JSON: {exc}") from exc
        return self._normalize(data, cpf_norm)

    def _normalize(self, raw: dict[str, Any], cpf: str) -> dict[str, Any]:
        # A resposta real tem muitos campos -- expomos apenas o subset
        # que importa pra ficha de admissao. Quando o plano for contratado
        # e a empresa receber a doc oficial, ajustamos o mapeamento.
        return {
            "cpf": cpf,
            "nome": raw.get("nome") or raw.get("nomeCompleto"),
            "data_nascimento": raw.get("dataNascimento")
            or raw.get("data_nascimento"),
            "sexo": raw.get("sexo"),
            "nome_mae": raw.get("nomeMae") or raw.get("nome_mae"),
            "situacao_cpf": raw.get("situacaoCadastral")
            or raw.get("situacao_cadastral"),
            "raw": raw,  # mantem o original para debug/auditoria
            "source": "directdata",
        }

    def _mock_response(self, cpf: str) -> dict[str, Any]:
        # Hash deterministico permite testes reprodutiveis. Nada de dado
        # real -- a UI deve marcar visualmente como "mock" via campo source.
        digest = hashlib.sha1(cpf.encode()).hexdigest()
        idx = int(digest[:2], 16)
        nomes = [
            "Joao da Silva",
            "Maria Souza",
            "Carlos Pereira",
            "Ana Lima",
            "Pedro Santos",
        ]
        return {
            "cpf": cpf,
            "nome": nomes[idx % len(nomes)],
            "data_nascimento": None,
            "sexo": None,
            "nome_mae": None,
            "situacao_cpf": "REGULAR",
            "raw": None,
            "source": "directdata_mock",
        }

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
