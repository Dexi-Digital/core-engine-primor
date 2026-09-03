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

from app.core.cpf import normalize_cpf
from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

TANGERINO_BASE_URL = "https://employer.tangerino.com.br"


def to_tangerino_date(valor: str) -> str:
    """Converte data ISO (`YYYY-MM-DD`) para o formato que a API aceita.

    O spec publico nao declara o formato. Testado contra a API REAL em
    27/08/2026 com credencial da Primor:

        startDate=2026-07-28  -> HTTP 400, BindException (typeMismatch)
        startDate=28/07/2026  -> passa o binding (404 so quando o
                                 funcionario nao tem batida no periodo)

    Ou seja: `dd/MM/yyyy`. A interface do adapter continua recebendo ISO
    (convencao do resto do repo) e converte aqui na borda. Valor ja no
    formato brasileiro passa intacto.
    """
    partes = valor.split("-")
    if len(partes) == 3 and len(partes[0]) == 4:
        ano, mes, dia = partes
        return f"{dia}/{mes}/{ano}"
    return valor


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
    {
        "id": 9001,
        "external_id": "OBRA-BR040-L3",
        "nome": "OBRA BR-040 LOTE 3",
        "ativo": True,
        "padrao": True,
    },
    {
        "id": 9002,
        "external_id": "OBRA-MG050-RC",
        "nome": "OBRA MG-050 RECAPEAMENTO",
        "ativo": True,
        "padrao": False,
    },
]
_MOCK_FUNCIONARIOS_NOMES = [
    ("JOSE DA SILVA", 0),  # obra A
    ("MARIA SOUZA", 0),  # obra A
    ("CARLOS SANTOS", 1),  # obra B
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
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)
        if not self._api_token:
            logger.info(
                "tangerino.mock_mode sem TANGERINO_API_KEY -- usando "
                "dataset mock deterministico"
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
        raw = await self._get_page(
            "/employee/find-all",
            page=page,
            size=size,
            showFired=1 if incluir_demitidos else 0,
        )
        return self._envelope(raw, page, size, self._normalize_funcionario)

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

        Datas entram em ISO `YYYY-MM-DD` e sao convertidas para
        `dd/MM/yyyy` na borda (ver `to_tangerino_date`) -- a API rejeita
        ISO com 400.

        Timestamps saem CRUS, em epoch de MILISSEGUNDOS (confirmado
        contra a API real em 27/08/2026: `birthDate=1123383600000`,
        `admissionDate=1787022000000`).

        Item: {employee_id, employee_external_id, data_trabalho_ts,
               inicio_ts, fim_ts, segundos_trabalhados, status, pis}
        """
        if self.is_mock:
            return self._mock_batidas(employee_id, start_date, end_date, page, size)
        raw = await self._get_page(
            f"/external/api/v1/payssego/punches/{employee_id}",
            page=page,
            size=size,
            vazio_em_404=True,
            startDate=to_tangerino_date(start_date),
            endDate=to_tangerino_date(end_date),
        )
        return self._envelope(raw, page, size, self._normalize_batida)

    async def list_locais_trabalho(self, *, page: int = 0, size: int = 100) -> dict[str, Any]:
        """Lista locais de trabalho (workplaces) -- vinculo com obras.

        Item: {id, external_id, nome, ativo, padrao}
        """
        if self.is_mock:
            return self._page_mock(list(_MOCK_WORKPLACES), page, size)
        raw = await self._get_page("/workplace/find-all", page=page, size=size)
        return self._envelope(raw, page, size, self._normalize_workplace)

    # --------------------------- HTTP interno ----------------------------

    def _auth_headers(self) -> dict[str, str]:
        # Header Authorization com o valor exatamente como a Solides
        # entrega. Testado contra a API real em 27/08/2026: funciona
        # tanto com o prefixo "Basic " quanto com a chave crua -- por
        # isso repassamos o valor sem tratar.
        return {"Authorization": self._api_token}

    async def _get_page(
        self,
        endpoint: str,
        *,
        page: int,
        size: int,
        vazio_em_404: bool = False,
        **params: Any,
    ) -> dict[str, Any]:
        # Paginacao: o par correto e `page`/`size` -- confirmado contra
        # a API REAL em 28/08/2026. `pageNumber`/`pageSize` sao
        # IGNORADOS e a resposta cai no default de 20 itens, o que
        # devolveria 20 de 396 funcionarios sem erro nenhum.
        #
        # ATENCAO: a API nao avanca pagina. `page`, `offset`, `start` e
        # `pageNumber` devolvem todos os MESMOS registros; so `size`
        # e honrado. Quem chama deve pedir tudo de uma vez com `size`
        # grande e conferir `totalElements` -- ver
        # `app.modules.ponto.service._buscar_tudo`.
        query = {"page": page, "size": size, **params}
        try:
            r = await self._client.get(endpoint, params=query, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise TangerinoError(f"transporte Tangerino ({endpoint}): {exc}") from exc
        if r.status_code in (401, 403):
            raise TangerinoAuthError(
                f"Tangerino {endpoint} status {r.status_code}: api key invalida ou sem permissao"
            )
        if r.status_code == 404 and vazio_em_404:
            # "Cant find punches for this employee" -- observado contra a
            # API real em 27/08/2026. E AUSENCIA DE DADO, nao falha: o
            # funcionario simplesmente nao bateu ponto no periodo. Se
            # virasse erro, o pull noturno abortaria no primeiro dos ~396
            # funcionarios sem batida e os demais nem seriam lidos.
            logger.debug("tangerino: sem registros em %s (404)", endpoint)
            return {"content": [], "totalElements": 0}
        if r.status_code >= 300:
            raise TangerinoError(f"Tangerino {endpoint} status {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise TangerinoError(f"resposta nao-JSON Tangerino ({endpoint}): {exc}") from exc
        if not isinstance(data, dict) or "content" not in data:
            raise TangerinoError(
                f"Tangerino {endpoint}: pagina Spring esperada, veio {type(data).__name__}"
            )
        return data

    def _envelope(
        self, raw: dict[str, Any], page: int, size: int, normalize: Any
    ) -> dict[str, Any]:
        return {
            "items": [normalize(item) for item in raw.get("content") or []],
            "total": int(raw.get("totalElements") or 0),
            "page": page,
            "size": size,
            "source": "tangerino",
        }

    @staticmethod
    def _normalize_funcionario(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw.get("id"),
            "external_id": raw.get("externalId"),
            "nome": raw.get("name"),
            "cpf": normalize_cpf(raw.get("cpf") or "") or None,
            "pis": raw.get("pis"),
            "admissao": raw.get("admissionDate"),
            "demitido": bool(raw.get("fired")),
            "workplaces": [
                {
                    "id": wp.get("id"),
                    "external_id": wp.get("externalId"),
                    "nome": wp.get("name"),
                }
                for wp in raw.get("workplaceList") or []
            ],
        }

    @staticmethod
    def _normalize_batida(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "employee_id": raw.get("employeeId"),
            "employee_external_id": raw.get("employeeExternalId"),
            "data_trabalho_ts": raw.get("dateWorked"),
            "inicio_ts": raw.get("startDateTimestamp"),
            "fim_ts": raw.get("endDateTimestamp"),
            "segundos_trabalhados": raw.get("workedTimeInSeconds"),
            "status": raw.get("status"),
            "pis": raw.get("pis"),
        }

    @staticmethod
    def _normalize_workplace(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw.get("id"),
            "external_id": raw.get("externalId"),
            "nome": raw.get("name"),
            "ativo": bool(raw.get("active")),
            "padrao": bool(raw.get("standard")),
        }

    # ------------------------------ mock ---------------------------------

    def _page_mock(self, items: list[dict[str, Any]], page: int, size: int) -> dict[str, Any]:
        start = page * size
        return {
            "items": items[start : start + size],
            "total": len(items),
            "page": page,
            "size": size,
            "source": "tangerino_mock",
        }

    def _mock_funcionarios(self, page: int, size: int, incluir_demitidos: bool) -> dict[str, Any]:
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
        digest = hashlib.sha1(f"tangerino|punch|{employee_id}|{start_date}".encode()).hexdigest()
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
