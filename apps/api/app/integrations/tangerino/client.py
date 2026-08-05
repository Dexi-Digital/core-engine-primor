"""Tangerino/Solides -- ponto eletronico (Modulos A e B).

O Tangerino (hoje Solides Ponto) guarda funcionarios, batidas de ponto
e locais de trabalho (workplaces). E a fonte para a apropriacao de mao
de obra por obra: o vinculo funcionario -> obra vem do WORKPLACE do
funcionario (workplaceList/currentWorkplaceDTO).

Endpoints usados (validados contra o spec publico
https://employer.tangerino.com.br/v2/api-docs em 2026-08-04):

- `GET /employee/find-all`                              -- funcionarios
- `GET /external/api/v1/payssego/punches/{employeeId}`  -- batidas
- `GET /workplace/find-all`                             -- locais de trabalho

Particularidades do spec:

- Auth e apiKey CRUA no header `Authorization` (securityDefinition
  "Token Access", sem prefixo Bearer). Se a API real exigir prefixo,
  ajustar apenas `_auth_headers`.
- Paginas sao Spring Data (`content`, `totalElements`), como OnSafety.
- `PunchSimpleDTO` NAO tem geolocalizacao -- nem nenhum outro modelo
  do spec publico. Nao ha endpoint de afastamentos. A unidade dos
  timestamps (epoch ms assumido) e o formato de startDate/endDate
  ("yyyy-MM-dd" assumido) nao sao declarados no spec -- a confirmar
  com credencial real; por isso os timestamps sao expostos CRUS.

Padrao do projeto: sem token (`TANGERINO_API_KEY` vazio), o adapter cai
num mock deterministico -- mesmo padrao OnSafety/Dominio/OneDrive.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

TANGERINO_BASE_URL = "https://employer.tangerino.com.br"


def _mock_cpf(digest: str) -> str:
    """CPF deterministico com digitos verificadores VALIDOS.

    Mesmo racional do mock OnSafety: consumidores validam CPF antes de
    casar com dp_employees; CPF invalido descartaria o dataset em
    silencio.
    """
    base = [int(d) for d in str(int(digest[:12], 16) % 10**9).zfill(9)]
    resto = sum(d * (10 - i) for i, d in enumerate(base)) % 11
    base.append(0 if resto < 2 else 11 - resto)
    resto = sum(d * (11 - i) for i, d in enumerate(base)) % 11
    base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


class TangerinoError(RuntimeError):
    """Transporte, status nao-2xx ou payload inesperado do Tangerino."""


class TangerinoAuthError(TangerinoError):
    """401/403 -- api key invalida ou sem permissao."""


# Dataset mock: 2 obras e 3 funcionarios (2 na obra A, 1 na obra B) --
# suficiente para a squad de mao de obra testar rateio por obra.
_MOCK_WORKPLACES = [
    {"id": 9001, "external_id": "OBRA-BR040-L3", "nome": "OBRA BR-040 LOTE 3",
     "ativo": True, "padrao": True},
    {"id": 9002, "external_id": "OBRA-MG050-RC", "nome": "OBRA MG-050 RECAPEAMENTO",
     "ativo": True, "padrao": False},
]
_MOCK_FUNCIONARIOS_NOMES = [
    ("JOSE DA SILVA", 0),      # obra A
    ("MARIA SOUZA", 0),        # obra A
    ("CARLOS SANTOS", 1),      # obra B
]


class TangerinoClient(IntegrationClient):
    name = "tangerino"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        base_url: str = TANGERINO_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_token = api_token or ""
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout
        )

    @property
    def is_mock(self) -> bool:
        return not self._api_token

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        if self.is_mock:
            return True
        try:
            await self.list_funcionarios(page=0, size=1)
        except TangerinoError:
            return False
        return True

    # ------------------------------ pull ---------------------------------

    async def list_funcionarios(
        self,
        *,
        page: int = 0,
        size: int = 100,
        incluir_demitidos: bool = False,
    ) -> dict[str, Any]:
        """Lista funcionarios do empregador.

        Item: {id, external_id, nome, cpf (11 digitos), pis, admissao,
               demitido, workplaces: [{id, external_id, nome}]}
        """
        if self.is_mock:
            return self._mock_funcionarios(page, size, incluir_demitidos)
        raise NotImplementedError  # Task 3

    async def list_batidas(
        self,
        employee_id: int,
        *,
        start_date: str,
        end_date: str,
        page: int = 0,
        size: int = 100,
    ) -> dict[str, Any]:
        """Lista batidas de ponto de um funcionario no periodo.

        Datas em ISO `YYYY-MM-DD`. Timestamps expostos CRUS (epoch
        assumido -- unidade a confirmar com credencial real).

        Item: {employee_id, employee_external_id, data_trabalho_ts,
               inicio_ts, fim_ts, segundos_trabalhados, status, pis}
        """
        if self.is_mock:
            return self._mock_batidas(
                employee_id, start_date, end_date, page, size
            )
        raise NotImplementedError  # Task 3

    async def list_locais_trabalho(
        self, *, page: int = 0, size: int = 100
    ) -> dict[str, Any]:
        """Lista locais de trabalho (workplaces) -- vinculo com obras.

        Item: {id, external_id, nome, ativo, padrao}
        """
        if self.is_mock:
            return self._page_mock(list(_MOCK_WORKPLACES), page, size)
        raise NotImplementedError  # Task 3

    # ------------------------------ mock ---------------------------------

    def _page_mock(
        self, items: list[dict[str, Any]], page: int, size: int
    ) -> dict[str, Any]:
        start = page * size
        return {
            "items": items[start : start + size],
            "total": len(items),
            "page": page,
            "size": size,
            "source": "tangerino_mock",
        }

    def _mock_funcionarios(
        self, page: int, size: int, incluir_demitidos: bool
    ) -> dict[str, Any]:
        del incluir_demitidos  # dataset mock nao tem demitidos
        items = []
        for i, (nome, wp_idx) in enumerate(_MOCK_FUNCIONARIOS_NOMES):
            digest = hashlib.sha1(f"tangerino|func|{i}".encode()).hexdigest()
            wp = _MOCK_WORKPLACES[wp_idx]
            items.append(
                {
                    "id": 100 + i,
                    "external_id": f"EXT-{1000 + i}",
                    "nome": nome,
                    "cpf": _mock_cpf(digest),
                    "pis": str(12000000000 + int(digest[:6], 16)),
                    "admissao": f"202{i % 3}-03-01",
                    "demitido": False,
                    "workplaces": [
                        {
                            "id": wp["id"],
                            "external_id": wp["external_id"],
                            "nome": wp["nome"],
                        }
                    ],
                }
            )
        return self._page_mock(items, page, size)

    def _mock_batidas(
        self,
        employee_id: int,
        start_date: str,
        end_date: str,
        page: int,
        size: int,
    ) -> dict[str, Any]:
        # 2 batidas por funcionario/consulta, funcao apenas de
        # (employee_id, start_date): deterministico e estavel.
        digest = hashlib.sha1(
            f"tangerino|punch|{employee_id}|{start_date}".encode()
        ).hexdigest()
        del end_date
        base_ts = 1_754_000_000_000 + (int(digest[:6], 16) % 86_400_000)
        items = []
        for j in range(2):
            inicio = base_ts + j * 43_200_000  # +12h por batida
            trabalhado = 4 * 3600 + (int(digest[6 + j], 16) % 3600)
            items.append(
                {
                    "employee_id": employee_id,
                    "employee_external_id": f"EXT-{900 + employee_id}",
                    "data_trabalho_ts": inicio,
                    "inicio_ts": inicio,
                    "fim_ts": inicio + trabalhado * 1000,
                    "segundos_trabalhados": trabalhado,
                    "status": "CLOSED",
                    "pis": None,
                }
            )
        return self._page_mock(items, page, size)
