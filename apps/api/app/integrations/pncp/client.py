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

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.integrations.base import IntegrationClient

PNCP_BASE_URL = "https://pncp.gov.br/api/consulta"
# The consulta API doesn't list documents; the separate portal API does.
# Pattern (verified live 2026-04): https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos
PNCP_PORTAL_BASE_URL = "https://pncp.gov.br/api/pncp"

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


@dataclass(slots=True)
class PncpArquivo:
    """One document attached to a contratacao (edital, projeto, termo etc)."""

    sequencial_documento: int
    titulo: str | None
    tipo_documento_descricao: str | None
    url: str
    status_ativo: bool
    data_publicacao_pncp: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PncpArquivo:
        return cls(
            sequencial_documento=int(data.get("sequencialDocumento") or 0),
            titulo=data.get("titulo"),
            tipo_documento_descricao=(
                data.get("tipoDocumentoDescricao") or data.get("tipoDocumentoNome")
            ),
            url=data.get("url") or data.get("uri") or "",
            status_ativo=bool(data.get("statusAtivo", True)),
            data_publicacao_pncp=data.get("dataPublicacaoPncp"),
        )


@dataclass(slots=True)
class PncpItem:
    """One item of a contratacao (portal API /itens)."""

    numero_item: int
    descricao: str | None
    tem_resultado: bool
    valor_total: float | None
    situacao_nome: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PncpItem:
        return cls(
            numero_item=int(data.get("numeroItem") or 0),
            descricao=data.get("descricao"),
            tem_resultado=bool(data.get("temResultado", False)),
            valor_total=_as_float(data.get("valorTotal")),
            situacao_nome=data.get("situacaoCompraItemNome"),
            raw=data,
        )


@dataclass(slots=True)
class PncpItemResultado:
    """Winner/homologation record for one item (portal API /resultados)."""

    sequencial_resultado: int
    ni_fornecedor: str | None
    nome_razao_social_fornecedor: str | None
    valor_total_homologado: float | None
    valor_unitario_homologado: float | None
    quantidade_homologada: float | None
    data_resultado: str | None
    situacao_nome: str | None
    porte_fornecedor_nome: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PncpItemResultado:
        return cls(
            sequencial_resultado=int(data.get("sequencialResultado") or 0),
            ni_fornecedor=data.get("niFornecedor"),
            nome_razao_social_fornecedor=data.get("nomeRazaoSocialFornecedor"),
            valor_total_homologado=_as_float(data.get("valorTotalHomologado")),
            valor_unitario_homologado=_as_float(data.get("valorUnitarioHomologado")),
            quantidade_homologada=_as_float(data.get("quantidadeHomologada")),
            data_resultado=data.get("dataResultado"),
            situacao_nome=data.get("situacaoCompraItemResultadoNome"),
            porte_fornecedor_nome=data.get("porteFornecedorNome"),
            raw=data,
        )


@dataclass(slots=True)
class PncpAta:
    """Ata de Registro de Preco (consulta API /v1/atas)."""

    numero_controle_pncp_ata: str | None
    numero_ata: str | None
    ano_ata: int | None
    numero_controle_pncp_compra: str | None
    cancelado: bool
    data_assinatura: str | None
    vigencia_inicio: str | None
    vigencia_fim: str | None
    objeto_contratacao: str | None
    cnpj_orgao: str | None
    nome_orgao: str | None
    possibilidade_adesao: bool | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PncpAta:
        return cls(
            numero_controle_pncp_ata=data.get("numeroControlePNCPAta"),
            numero_ata=data.get("numeroAtaRegistroPreco"),
            ano_ata=data.get("anoAta"),
            numero_controle_pncp_compra=data.get("numeroControlePNCPCompra"),
            cancelado=bool(data.get("cancelado", False)),
            data_assinatura=data.get("dataAssinatura"),
            vigencia_inicio=data.get("vigenciaInicio"),
            vigencia_fim=data.get("vigenciaFim"),
            objeto_contratacao=data.get("objetoContratacao"),
            cnpj_orgao=data.get("cnpjOrgao"),
            nome_orgao=data.get("nomeOrgao"),
            possibilidade_adesao=data.get("possibilidadeAdesao"),
            raw=data,
        )


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
        portal_base_url: str = PNCP_PORTAL_BASE_URL,
        portal_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._portal_base_url = portal_base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._portal_client = portal_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def _get_portal_client(self) -> httpx.AsyncClient:
        if self._portal_client is None:
            self._portal_client = httpx.AsyncClient(
                base_url=self._portal_base_url, timeout=self._timeout
            )
        return self._portal_client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._portal_client is not None:
            await self._portal_client.aclose()
            self._portal_client = None

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

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    async def list_arquivos(
        self, *, cnpj: str, ano: int, sequencial: int
    ) -> list[PncpArquivo]:
        """List the documents PNCP has stored for this contratacao.

        Returns an empty list when PNCP responds 204 or 404. Other errors
        bubble up so the caller can fall back to a different portal.
        """
        client = await self._get_portal_client()
        path = f"/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
        resp = await client.get(path)
        if resp.status_code in (204, 404):
            return []
        resp.raise_for_status()
        body = resp.json() or []
        return [PncpArquivo.from_api(d) for d in body if d]

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    async def list_itens(
        self, *, cnpj: str, ano: int, sequencial: int
    ) -> list[PncpItem]:
        """List items of a contratacao. 204/404 -> empty list."""
        client = await self._get_portal_client()
        path = f"/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens"
        resp = await client.get(path)
        if resp.status_code in (204, 404):
            return []
        resp.raise_for_status()
        body = resp.json() or []
        return [PncpItem.from_api(d) for d in body if d]

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    async def list_item_resultados(
        self, *, cnpj: str, ano: int, sequencial: int, numero_item: int
    ) -> list[PncpItemResultado]:
        """List homologation results of one item. 204/404 -> empty list."""
        client = await self._get_portal_client()
        path = f"/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens/{numero_item}/resultados"
        resp = await client.get(path)
        if resp.status_code in (204, 404):
            return []
        resp.raise_for_status()
        body = resp.json() or []
        return [PncpItemResultado.from_api(d) for d in body if d]

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, max=15),
        reraise=True,
    )
    async def list_atas(
        self,
        *,
        data_inicial: date | str,
        data_final: date | str,
        cnpj: str | None = None,
        pagina: int = 1,
        tamanho_pagina: int = 50,
    ) -> tuple[list[PncpAta], int]:
        """Fetch one page of /v1/atas. Returns (atas, paginas_restantes)."""
        params: dict[str, Any] = {
            "dataInicial": _fmt_date(data_inicial),
            "dataFinal": _fmt_date(data_final),
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }
        if cnpj:
            params["cnpj"] = cnpj
        client = await self._get_client()
        resp = await client.get("/v1/atas", params=params)
        if resp.status_code == 204:
            return [], 0
        resp.raise_for_status()
        body = resp.json()
        atas = [PncpAta.from_api(d) for d in body.get("data", [])]
        if body.get("empty", not atas):
            return atas, 0
        return atas, body.get("paginasRestantes", 0)

    async def iter_atas(
        self,
        *,
        data_inicial: date | str,
        data_final: date | str,
        cnpj: str | None = None,
        tamanho_pagina: int = 50,
        max_paginas: int | None = None,
    ) -> AsyncIterator[PncpAta]:
        """Async generator over every ata page in the window."""
        pagina = 1
        while True:
            atas, restantes = await self.list_atas(
                data_inicial=data_inicial,
                data_final=data_final,
                cnpj=cnpj,
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
            )
            for ata in atas:
                yield ata
            if restantes <= 0 or (max_paginas and pagina >= max_paginas):
                return
            pagina += 1

    async def stream_arquivo(self, url: str) -> tuple[AsyncIterator[bytes], str, str | None]:
        """Stream one arquivo; returns `(iterator, filename, content_type)`.

        The caller is responsible for closing the underlying response via
        the `aclose` method on the returned iterator's `.response`. To keep
        the API simple we return a lightweight async iterator that owns
        the response and closes it on exhaustion / exception.
        """
        client = await self._get_portal_client()
        # We must open a streaming request; httpx exposes `.stream` on the
        # client which returns an async context manager. We wrap it in an
        # iterator so the service layer can treat it as a generic source.
        req = client.build_request("GET", url)
        resp = await client.send(req, stream=True)
        try:
            resp.raise_for_status()
        except BaseException:
            # raise_for_status raises before _iter() is created, so the
            # caller has no chance to aclose() the streamed response.
            # Close it here to avoid leaking one connection per failed file.
            await resp.aclose()
            raise
        filename = _extract_filename(resp.headers) or "anexo.pdf"
        content_type = resp.headers.get("content-type")

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        return _iter(), filename, content_type


def _extract_filename(headers: httpx.Headers) -> str | None:
    """Pull a filename out of a Content-Disposition header (best-effort)."""
    raw = headers.get("content-disposition")
    if not raw:
        return None
    # Very small parser: look for filename="..." or filename=...
    marker = "filename="
    idx = raw.lower().find(marker)
    if idx == -1:
        return None
    value = raw[idx + len(marker) :].strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        if end > 0:
            return value[1:end]
    return value.split(";")[0].strip() or None
