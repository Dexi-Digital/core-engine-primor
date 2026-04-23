"""PNCP (Portal Nacional de Contratacoes Publicas) consulta-api client.

Swagger: https://pncp.gov.br/api/consulta/swagger-ui/index.html
Base URL: https://pncp.gov.br/api/consulta

Only implements the subset we need now:
    GET /v1/contratacoes/publicacao  (list by publication date)
    GET /v1/orgaos/{cnpj}/compras/{ano}/{sequencial}  (single detail)

Dates are always yyyyMMdd strings. `codigoModalidadeContratacao` is required
by the API on the list endpoint, so we iterate known modalidades when
crawling for a full window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.integrations.base import IntegrationClient

PNCP_BASE_URL = "https://pncp.gov.br/api/consulta"

# Subset of PNCP modalidades (see PNCP manual section "Tabelas de Dominio").
# Kept as a tuple so consumers can iterate when scraping a whole window.
MODALIDADES: tuple[int, ...] = (
    1,   # Leilao Eletronico
    2,   # Dialogo Competitivo
    3,   # Concurso
    4,   # Concorrencia Eletronica
    5,   # Concorrencia Presencial
    6,   # Pregao Eletronico
    7,   # Pregao Presencial
    8,   # Dispensa de Licitacao
    9,   # Inexigibilidade
    10,  # Manifestacao de Interesse
    11,  # Pre-qualificacao
    12,  # Credenciamento
    13,  # Leilao Presencial
)


@dataclass(slots=True)
class PncpOrgao:
    cnpj: str | None = None
    razao_social: str | None = None
    poder_id: str | None = None
    esfera_id: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> PncpOrgao | None:
        if not data:
            return None
        return cls(
            cnpj=data.get("cnpj"),
            razao_social=data.get("razaoSocial"),
            poder_id=data.get("poderId"),
            esfera_id=data.get("esferaId"),
        )


@dataclass(slots=True)
class PncpUnidade:
    uf_sigla: str | None = None
    uf_nome: str | None = None
    municipio_nome: str | None = None
    codigo_ibge: str | None = None
    nome_unidade: str | None = None
    codigo_unidade: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> PncpUnidade | None:
        if not data:
            return None
        return cls(
            uf_sigla=data.get("ufSigla"),
            uf_nome=data.get("ufNome"),
            municipio_nome=data.get("municipioNome"),
            codigo_ibge=data.get("codigoIbge"),
            nome_unidade=data.get("nomeUnidade"),
            codigo_unidade=data.get("codigoUnidade"),
        )


@dataclass(slots=True)
class PncpPublicacao:
    numero_compra: str | None
    ano_compra: int | None
    sequencial_compra: int | None
    objeto_compra: str | None
    modalidade_nome: str | None
    modo_disputa_nome: str | None
    situacao_compra_nome: str | None
    valor_total_estimado: float | None
    valor_total_homologado: float | None
    srp: bool | None
    data_atualizacao: str | None
    data_publicacao_pncp: str | None
    orgao: PncpOrgao | None
    unidade: PncpUnidade | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PncpPublicacao:
        return cls(
            numero_compra=data.get("numeroCompra"),
            ano_compra=data.get("anoCompra"),
            sequencial_compra=data.get("sequencialCompra"),
            objeto_compra=data.get("objetoCompra"),
            modalidade_nome=data.get("modalidadeNome"),
            modo_disputa_nome=data.get("modoDisputaNome"),
            situacao_compra_nome=data.get("situacaoCompraNome"),
            valor_total_estimado=_as_float(data.get("valorTotalEstimado")),
            valor_total_homologado=_as_float(data.get("valorTotalHomologado")),
            srp=data.get("srp"),
            data_atualizacao=data.get("dataAtualizacao"),
            data_publicacao_pncp=data.get("dataPublicacaoPncp") or data.get("dataInclusao"),
            orgao=PncpOrgao.from_api(data.get("orgaoEntidade")),
            unidade=PncpUnidade.from_api(data.get("unidadeOrgao")),
            raw=data,
        )

    @property
    def external_id(self) -> str:
        """Stable identifier across the API's `orgao/ano/sequencial` triplet."""
        cnpj = (self.orgao.cnpj if self.orgao else None) or "unknown"
        return f"{cnpj}-{self.ano_compra}-{self.sequencial_compra}"


@dataclass(slots=True)
class PncpPage:
    data: list[PncpPublicacao]
    total_registros: int
    total_paginas: int
    numero_pagina: int
    paginas_restantes: int
    empty: bool


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_date(d: date | str) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


class PncpClient(IntegrationClient):
    """Read-only HTTP client for PNCP Consulta API."""

    name = "pncp"

    def __init__(
        self,
        base_url: str = PNCP_BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            r = await client.get("/v3/api-docs", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, max=15),
        reraise=True,
    )
    async def list_contratacoes_por_publicacao(
        self,
        *,
        data_inicial: date | str,
        data_final: date | str,
        codigo_modalidade: int,
        uf: str | None = None,
        codigo_municipio_ibge: str | None = None,
        cnpj: str | None = None,
        pagina: int = 1,
        tamanho_pagina: int = 50,
    ) -> PncpPage:
        """Fetch one page from /v1/contratacoes/publicacao.

        The PNCP API requires `codigoModalidadeContratacao`, so callers must
        loop through `MODALIDADES` to cover every kind of procurement.
        """
        params: dict[str, Any] = {
            "dataInicial": _fmt_date(data_inicial),
            "dataFinal": _fmt_date(data_final),
            "codigoModalidadeContratacao": codigo_modalidade,
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }
        if uf:
            params["uf"] = uf
        if codigo_municipio_ibge:
            params["codigoMunicipioIbge"] = codigo_municipio_ibge
        if cnpj:
            params["cnpj"] = cnpj

        client = await self._get_client()
        resp = await client.get("/v1/contratacoes/publicacao", params=params)

        if resp.status_code == 204:
            return PncpPage(
                data=[],
                total_registros=0,
                total_paginas=0,
                numero_pagina=pagina,
                paginas_restantes=0,
                empty=True,
            )
        resp.raise_for_status()
        body = resp.json()
        return PncpPage(
            data=[PncpPublicacao.from_api(d) for d in body.get("data", [])],
            total_registros=body.get("totalRegistros", 0),
            total_paginas=body.get("totalPaginas", 0),
            numero_pagina=body.get("numeroPagina", pagina),
            paginas_restantes=body.get("paginasRestantes", 0),
            empty=body.get("empty", not body.get("data")),
        )

    async def iter_contratacoes_por_publicacao(
        self,
        *,
        data_inicial: date | str,
        data_final: date | str,
        codigo_modalidade: int,
        uf: str | None = None,
        codigo_municipio_ibge: str | None = None,
        cnpj: str | None = None,
        tamanho_pagina: int = 50,
        max_paginas: int | None = None,
    ):
        """Async generator that paginates until the API runs out of pages."""
        pagina = 1
        while True:
            page = await self.list_contratacoes_por_publicacao(
                data_inicial=data_inicial,
                data_final=data_final,
                codigo_modalidade=codigo_modalidade,
                uf=uf,
                codigo_municipio_ibge=codigo_municipio_ibge,
                cnpj=cnpj,
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
            )
            for item in page.data:
                yield item
            if page.empty or page.paginas_restantes <= 0:
                return
            if max_paginas and pagina >= max_paginas:
                return
            pagina += 1
