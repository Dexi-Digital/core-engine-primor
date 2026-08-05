# Squad 3 — Resultados PNCP + Dashboards Comerciais — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingerir resultados/homologações e atas de registro de preço do PNCP e expor 4 dashboards de inteligência comercial (concorrentes por CNPJ, geotargeting MG, não captados, eficiência operacional) — seção 9 do Projeto Técnico do Captador + D.9 do roadmap.

**Architecture:** Extensão do módulo `apps/api/app/modules/licitacoes/` seguindo os padrões já estabelecidos: novos métodos no `PncpClient` (API consulta + API portal, ambas públicas e sem autenticação), novos models com upsert idempotente, endpoints de ingestão manual (mesmo padrão do `POST /ingest/pncp` — sem Celery, compatível com o deploy Vercel), e dashboards como agregações SQL servidas por endpoints GET consumidos por uma página Next.js server-component.

**Tech Stack:** FastAPI (async), SQLAlchemy 2.0 (`mapped_column`), httpx + MockTransport nos testes, Alembic, pytest-asyncio (SQLite in-memory), Next.js App Router (server components, Tailwind).

## Global Constraints

- Rodar testes de dentro de `apps/api`: `uv run --extra dev pytest <arquivo>::<teste> -v` (suite completa: `uv run --extra dev pytest`).
- Lint: `uv run --extra dev ruff check .` deve passar limpo antes de cada commit.
- Todo endpoint que **muta** recurso exige `Depends(get_current_user)` (grava actor no audit; padrão do repo). Endpoints GET de leitura seguem o padrão dos GETs existentes do módulo (sem auth).
- Migrations: `down_revision` do head atual é `"b8c9d0e1f2a3"` — **CONFERIR no momento da implementação** (`ls apps/api/alembic/versions/` e recalcular o head), pois as Squads 1 e 2 desta sprint também criam migrations em paralelo. Quem mergear por último encadeia no head do outro.
- Datas PNCP chegam como strings ISO — usar o helper `_parse_dt` existente em `app/modules/licitacoes/service.py`.
- Commits: mensagens em pt-BR estilo `feat(módulo D): ...` seguindo o histórico do repo, com `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Dependência entre squads (Tasks 7–8):** a Task 7 consome `Licitacao.status_triagem` (String) e o model `DecisaoTriagem` (tabela `licitacoes_decisoes_triagem`) criados pela **Squad 1**, e `PlanilhaOrcamentaria` (tabela `licitacoes_planilhas_orcamentarias`, campo `status_validacao`) criada pela **Squad 2**. Tasks 1–6 e 8–9 não dependem de nada externo. Se Squad 1/2 ainda não mergearam quando você chegar na Task 7, pule-a e registre no PR que ela fica para um follow-up.

## Referência de API (verificado live em 2026-08-04 contra os OpenAPI specs do PNCP)

- **Itens de uma contratação** (API portal, mesma base do download de anexos): `GET {PNCP_PORTAL_BASE_URL}/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens` → array de objetos com `numeroItem`, `descricao`, `temResultado` (bool), `valorTotal`, `situacaoCompraItemNome`.
- **Resultados de um item**: `GET {PNCP_PORTAL_BASE_URL}/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens/{numeroItem}/resultados` → array com `sequencialResultado`, `niFornecedor`, `nomeRazaoSocialFornecedor`, `valorTotalHomologado`, `valorUnitarioHomologado`, `quantidadeHomologada`, `dataResultado`, `situacaoCompraItemResultadoNome`, `porteFornecedorNome`, `numeroControlePNCPCompra`.
- **Atas de RP** (API consulta): `GET {PNCP_BASE_URL}/v1/atas?dataInicial=yyyyMMdd&dataFinal=yyyyMMdd&pagina=N` (paginação igual a `/v1/contratacoes/publicacao`) → página com `data[]` de `numeroControlePNCPAta`, `numeroAtaRegistroPreco`, `anoAta`, `numeroControlePNCPCompra`, `cancelado`, `vigenciaInicio`, `vigenciaFim`, `objetoContratacao`, `cnpjOrgao`, `nomeOrgao`, `possibilidadeAdesao`, `dataAssinatura`. **Não há campo de valor** na ata — a coluna `valor` fica nullable e é preenchida via join com a licitação vinculada quando possível.
- Formato de `numeroControlePNCPCompra`: `"{cnpj14}-1-{sequencial}/{ano}"` (ex.: `00394460000141-1-000156/2024`) — converter para o `external_id` do repo (`{cnpj}-{ano}-{seq}` com ints sem zero-pad).

---

### Task 1: PncpClient — itens e resultados de item

**Files:**
- Modify: `apps/api/app/integrations/pncp/client.py`
- Test: `apps/api/tests/test_pncp_client.py`

**Interfaces:**
- Consumes: `PncpClient._get_portal_client()`, padrão de dataclasses `from_api` existentes.
- Produces: `PncpItem` (campos `numero_item: int`, `descricao: str | None`, `tem_resultado: bool`, `valor_total: float | None`, `situacao_nome: str | None`, `raw: dict`), `PncpItemResultado` (campos `sequencial_resultado: int`, `ni_fornecedor: str | None`, `nome_razao_social_fornecedor: str | None`, `valor_total_homologado: float | None`, `valor_unitario_homologado: float | None`, `quantidade_homologada: float | None`, `data_resultado: str | None`, `situacao_nome: str | None`, `porte_fornecedor_nome: str | None`, `raw: dict`), métodos `PncpClient.list_itens(*, cnpj: str, ano: int, sequencial: int) -> list[PncpItem]` e `PncpClient.list_item_resultados(*, cnpj: str, ano: int, sequencial: int, numero_item: int) -> list[PncpItemResultado]`.

- [ ] **Step 1: Write the failing tests**

Adicionar ao final de `apps/api/tests/test_pncp_client.py`:

```python
def _portal_transport(handler):
    return httpx.AsyncClient(base_url="https://mockportal.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_itens_maps_fields_and_handles_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/compras/2024/9/" in str(request.url) or str(request.url).endswith("/compras/2024/9/itens"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            json=[
                {
                    "numeroItem": 1,
                    "descricao": "Recapeamento asfaltico",
                    "temResultado": True,
                    "valorTotal": 1500000.5,
                    "situacaoCompraItemNome": "Homologado",
                },
                {"numeroItem": 2, "descricao": "Sinalizacao", "temResultado": False},
            ],
        )

    client = PncpClient(portal_base_url="https://mockportal.test", portal_client=_portal_transport(handler))
    itens = await client.list_itens(cnpj="00394460000141", ano=2024, sequencial=156)
    assert len(itens) == 2
    assert itens[0].numero_item == 1
    assert itens[0].tem_resultado is True
    assert itens[0].valor_total == 1500000.5
    assert itens[1].tem_resultado is False

    vazio = await client.list_itens(cnpj="00394460000141", ano=2024, sequencial=9)
    assert vazio == []
    await client.aclose()


@pytest.mark.asyncio
async def test_list_item_resultados_maps_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/orgaos/00394460000141/compras/2024/156/itens/1/resultados")
        return httpx.Response(
            200,
            json=[
                {
                    "sequencialResultado": 1,
                    "niFornecedor": "11222333000144",
                    "nomeRazaoSocialFornecedor": "Construtora Alfa LTDA",
                    "valorTotalHomologado": 1450000.0,
                    "valorUnitarioHomologado": 145.0,
                    "quantidadeHomologada": 10000.0,
                    "dataResultado": "2024-11-05",
                    "situacaoCompraItemResultadoNome": "Informado",
                    "porteFornecedorNome": "Demais",
                }
            ],
        )

    client = PncpClient(portal_base_url="https://mockportal.test", portal_client=_portal_transport(handler))
    rows = await client.list_item_resultados(cnpj="00394460000141", ano=2024, sequencial=156, numero_item=1)
    assert len(rows) == 1
    assert rows[0].ni_fornecedor == "11222333000144"
    assert rows[0].valor_total_homologado == 1450000.0
    assert rows[0].sequencial_resultado == 1
    await client.aclose()
```

(`import httpx`, `import pytest` e `from app.integrations.pncp.client import PncpClient` já existem no arquivo.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_pncp_client.py -v -k "itens or resultados"`
Expected: FAIL com `AttributeError: 'PncpClient' object has no attribute 'list_itens'`

- [ ] **Step 3: Implement dataclasses + methods**

Em `apps/api/app/integrations/pncp/client.py`, adicionar após `PncpArquivo`:

```python
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
```

E na classe `PncpClient`, após `list_arquivos` (mesmo padrão de retry e 204/404 → lista vazia):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_pncp_client.py -v`
Expected: PASS (novos e antigos)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/integrations/pncp/client.py apps/api/tests/test_pncp_client.py
git commit -m "feat(pncp): client de itens + resultados de item (API portal)"
```

---

### Task 2: PncpClient — atas de registro de preço

**Files:**
- Modify: `apps/api/app/integrations/pncp/client.py`
- Test: `apps/api/tests/test_pncp_client.py`

**Interfaces:**
- Produces: `PncpAta` (campos `numero_controle_pncp_ata: str | None`, `numero_ata: str | None`, `ano_ata: int | None`, `numero_controle_pncp_compra: str | None`, `cancelado: bool`, `data_assinatura: str | None`, `vigencia_inicio: str | None`, `vigencia_fim: str | None`, `objeto_contratacao: str | None`, `cnpj_orgao: str | None`, `nome_orgao: str | None`, `possibilidade_adesao: bool | None`, `raw: dict`), `PncpClient.iter_atas(*, data_inicial, data_final, cnpj=None, tamanho_pagina=50, max_paginas=None)` async generator de `PncpAta`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_iter_atas_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert request.url.path == "/v1/atas"
        assert params["dataInicial"] == "20260101"
        pagina = int(params["pagina"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "numeroControlePNCPAta": f"ata-{pagina}",
                        "numeroAtaRegistroPreco": f"00{pagina}/2026",
                        "anoAta": 2026,
                        "numeroControlePNCPCompra": "00394460000141-1-000156/2024",
                        "cancelado": False,
                        "vigenciaInicio": "2026-01-01",
                        "vigenciaFim": "2026-12-31",
                        "objetoContratacao": "Registro de precos de pavimentacao",
                        "cnpjOrgao": "00394460000141",
                        "nomeOrgao": "Prefeitura X",
                        "possibilidadeAdesao": True,
                    }
                ],
                "totalRegistros": 2,
                "totalPaginas": 2,
                "numeroPagina": pagina,
                "paginasRestantes": 2 - pagina,
                "empty": False,
            },
        )

    http = httpx.AsyncClient(base_url="https://mock.test", transport=httpx.MockTransport(handler))
    client = PncpClient(base_url="https://mock.test", client=http)
    atas = [a async for a in client.iter_atas(data_inicial="2026-01-01", data_final="2026-03-01")]
    assert len(atas) == 2
    assert atas[0].numero_controle_pncp_ata == "ata-1"
    assert atas[1].numero_controle_pncp_ata == "ata-2"
    assert atas[0].possibilidade_adesao is True
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_pncp_client.py::test_iter_atas_paginates -v`
Expected: FAIL com `AttributeError: ... no attribute 'iter_atas'`

- [ ] **Step 3: Implement**

Dataclass após `PncpItemResultado`:

```python
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
```

Métodos na classe (consulta API — usa `_get_client()`, não o portal):

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run --extra dev pytest tests/test_pncp_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/integrations/pncp/client.py apps/api/tests/test_pncp_client.py
git commit -m "feat(pncp): client de atas de registro de preco (/v1/atas)"
```

---

### Task 3: Models `ResultadoLicitacao` + `AtaRegistroPreco` + migration

**Files:**
- Modify: `apps/api/app/modules/licitacoes/models.py`
- Create: `apps/api/alembic/versions/a1b2c3d4e5f6_licitacoes_resultados_atas.py`
- Test: `apps/api/tests/test_licitacoes_resultados.py` (novo arquivo)

**Interfaces:**
- Consumes: `Base` de `app.core.db`, FK para `licitacoes.id`.
- Produces: `ResultadoLicitacao` (tabela `licitacoes_resultados`) e `AtaRegistroPreco` (tabela `licitacoes_atas_rp`) — nomes exatos usados nas Tasks 4–8.

- [ ] **Step 1: Write the failing test**

Criar `apps/api/tests/test_licitacoes_resultados.py`:

```python
"""Squad 3: resultados/homologacoes + atas RP + dashboards comerciais."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao, ResultadoLicitacao


async def _make_licitacao(db: AsyncSession, **overrides) -> Licitacao:
    defaults = dict(
        external_id="00394460000141-2024-156",
        source="pncp",
        numero_compra="156",
        ano_compra=2024,
        sequencial_compra=156,
        objeto_compra="Recapeamento asfaltico",
        modalidade_nome="Concorrencia Eletronica",
        orgao_cnpj="00394460000141",
        orgao_razao_social="Prefeitura X",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    defaults.update(overrides)
    lic = Licitacao(**defaults)
    db.add(lic)
    await db.flush()
    return lic


@pytest.mark.asyncio
async def test_resultado_and_ata_models_roundtrip(db_session: AsyncSession) -> None:
    lic = await _make_licitacao(db_session)
    db_session.add(
        ResultadoLicitacao(
            licitacao_id=lic.id,
            item_numero=1,
            sequencial_resultado=1,
            cnpj_vencedor="11222333000144",
            razao_social="Construtora Alfa LTDA",
            valor_homologado=Decimal("1450000.00"),
            data_resultado=datetime(2026, 7, 10, tzinfo=UTC),
            situacao="Informado",
            porte_fornecedor="Demais",
        )
    )
    db_session.add(
        AtaRegistroPreco(
            numero_controle_pncp_ata="00394460000141-1-000156/2024-001",
            numero_ata="001/2026",
            licitacao_id=lic.id,
            orgao_cnpj="00394460000141",
            orgao_nome="Prefeitura X",
            vigencia_inicio=datetime(2026, 1, 1, tzinfo=UTC).date(),
            vigencia_fim=datetime(2026, 12, 31, tzinfo=UTC).date(),
            objeto="Registro de precos de pavimentacao",
            possibilidade_adesao=True,
        )
    )
    await db_session.commit()

    r = (await db_session.execute(select(ResultadoLicitacao))).scalar_one()
    assert r.cnpj_vencedor == "11222333000144"
    a = (await db_session.execute(select(AtaRegistroPreco))).scalar_one()
    assert a.licitacao_id == lic.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_licitacoes_resultados.py -v`
Expected: FAIL com `ImportError: cannot import name 'ResultadoLicitacao'`

- [ ] **Step 3: Implement models**

Adicionar ao final de `apps/api/app/modules/licitacoes/models.py`:

```python
# --- Squad 3: resultados/homologacoes (secao 9 do Projeto Tecnico) ---------


class ResultadoLicitacao(Base):
    """Resultado homologado de um item de licitacao (fornecedor vencedor).

    Fonte: API portal do PNCP (`/itens/{n}/resultados`). Um item pode ter
    mais de um resultado (ordem de classificacao em SRP), por isso o
    unique inclui `sequencial_resultado`. Alimenta os dashboards de
    concorrentes e geotargeting.
    """

    __tablename__ = "licitacoes_resultados"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        index=True,
    )
    item_numero: Mapped[int] = mapped_column(Integer)
    sequencial_resultado: Mapped[int] = mapped_column(Integer, default=1)
    cnpj_vencedor: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    razao_social: Mapped[str | None] = mapped_column(String(512), nullable=True)
    valor_homologado: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    valor_unitario: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    data_resultado: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    situacao: Mapped[str | None] = mapped_column(String(64), nullable=True)
    porte_fornecedor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "licitacao_id", "item_numero", "sequencial_resultado",
            name="uq_resultado_item_seq",
        ),
        Index("ix_resultados_cnpj_data", "cnpj_vencedor", "data_resultado"),
    )


class AtaRegistroPreco(Base):
    """Ata de Registro de Preco captada do PNCP (D.9 do roadmap).

    `numero_controle_pncp_ata` e a chave natural do PNCP (upsert
    idempotente). `licitacao_id` e preenchido quando o
    `numeroControlePNCPCompra` bate com uma licitacao ja captada.
    `valor` nao vem na API de atas -- fica NULL ate cruzarmos com a
    licitacao vinculada.
    """

    __tablename__ = "licitacoes_atas_rp"

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_controle_pncp_ata: Mapped[str] = mapped_column(
        String(128), unique=True, index=True
    )
    numero_ata: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ano_ata: Mapped[int | None] = mapped_column(Integer, nullable=True)
    licitacao_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    orgao_cnpj: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    orgao_nome: Mapped[str | None] = mapped_column(String(512), nullable=True)
    objeto: Mapped[str | None] = mapped_column(Text, nullable=True)
    vigencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cancelado: Mapped[bool] = mapped_column(Boolean, default=False)
    possibilidade_adesao: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

(Todos os imports usados — `Decimal`, `date`, `Text`, `Date`, `Boolean` etc. — já estão no topo do arquivo.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_licitacoes_resultados.py -v`
Expected: PASS (o conftest cria as tabelas via `Base.metadata.create_all`)

- [ ] **Step 5: Write the Alembic migration**

Criar `apps/api/alembic/versions/a1b2c3d4e5f6_licitacoes_resultados_atas.py` (ATENÇÃO: confirmar `down_revision` contra o head real no momento da implementação):

```python
"""licitacoes: resultados homologados + atas de registro de preco (Squad 3)

Revision ID: a1b2c3d4e5f6
Revises: b8c9d0e1f2a3
Create Date: 2026-08-04
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_resultados",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_numero", sa.Integer(), nullable=False),
        sa.Column("sequencial_resultado", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cnpj_vencedor", sa.String(length=32), nullable=True),
        sa.Column("razao_social", sa.String(length=512), nullable=True),
        sa.Column("valor_homologado", sa.Numeric(20, 2), nullable=True),
        sa.Column("valor_unitario", sa.Numeric(20, 4), nullable=True),
        sa.Column("quantidade", sa.Numeric(20, 4), nullable=True),
        sa.Column("data_resultado", sa.DateTime(timezone=True), nullable=True),
        sa.Column("situacao", sa.String(length=64), nullable=True),
        sa.Column("porte_fornecedor", sa.String(length=64), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "licitacao_id", "item_numero", "sequencial_resultado",
            name="uq_resultado_item_seq",
        ),
    )
    op.create_index("ix_licitacoes_resultados_licitacao_id", "licitacoes_resultados", ["licitacao_id"])
    op.create_index("ix_licitacoes_resultados_cnpj_vencedor", "licitacoes_resultados", ["cnpj_vencedor"])
    op.create_index("ix_licitacoes_resultados_data_resultado", "licitacoes_resultados", ["data_resultado"])
    op.create_index("ix_resultados_cnpj_data", "licitacoes_resultados", ["cnpj_vencedor", "data_resultado"])

    op.create_table(
        "licitacoes_atas_rp",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("numero_controle_pncp_ata", sa.String(length=128), nullable=False),
        sa.Column("numero_ata", sa.String(length=64), nullable=True),
        sa.Column("ano_ata", sa.Integer(), nullable=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("orgao_cnpj", sa.String(length=32), nullable=True),
        sa.Column("orgao_nome", sa.String(length=512), nullable=True),
        sa.Column("objeto", sa.Text(), nullable=True),
        sa.Column("vigencia_inicio", sa.Date(), nullable=True),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("valor", sa.Numeric(20, 2), nullable=True),
        sa.Column("cancelado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("possibilidade_adesao", sa.Boolean(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("numero_controle_pncp_ata", name="uq_ata_numero_controle"),
    )
    op.create_index("ix_licitacoes_atas_rp_licitacao_id", "licitacoes_atas_rp", ["licitacao_id"])
    op.create_index("ix_licitacoes_atas_rp_orgao_cnpj", "licitacoes_atas_rp", ["orgao_cnpj"])
    op.create_index("ix_licitacoes_atas_rp_vigencia_fim", "licitacoes_atas_rp", ["vigencia_fim"])


def downgrade() -> None:
    op.drop_table("licitacoes_atas_rp")
    op.drop_table("licitacoes_resultados")
```

- [ ] **Step 6: Run the full suite (migration não roda em SQLite de teste, mas o import não pode quebrar nada)**

Run: `uv run --extra dev pytest tests/test_alembic_env_url.py tests/test_licitacoes_resultados.py -v && uv run --extra dev ruff check .`
Expected: PASS / limpo

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/models.py apps/api/alembic/versions/a1b2c3d4e5f6_licitacoes_resultados_atas.py apps/api/tests/test_licitacoes_resultados.py
git commit -m "feat(módulo D): models de resultados homologados + atas RP (Squad 3)"
```

---

### Task 4: Ingestão de resultados — service + endpoint

**Files:**
- Create: `apps/api/app/modules/licitacoes/resultados.py`
- Modify: `apps/api/app/modules/licitacoes/schemas.py`
- Modify: `apps/api/app/modules/licitacoes/router.py`
- Test: `apps/api/tests/test_licitacoes_resultados.py`

**Interfaces:**
- Consumes: `PncpClient.list_itens` / `list_item_resultados` (Task 1), `ResultadoLicitacao` (Task 3), `_parse_dt` de `service.py`.
- Produces: `ingest_resultados(db, client, *, dias=30, uf=None, max_licitacoes=200) -> ResultadoIngestSummary`; schema `ResultadoIngestSummary(licitacoes_processadas: int, com_resultado: int, resultados_gravados: int, falhas: int)`; endpoint `POST /api/v1/licitacoes/ingest/resultados`.

- [ ] **Step 1: Write the failing tests**

Adicionar em `apps/api/tests/test_licitacoes_resultados.py`:

```python
import httpx

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.resultados import ingest_resultados


def _pncp_portal_client(handler) -> PncpClient:
    http = httpx.AsyncClient(
        base_url="https://mockportal.test", transport=httpx.MockTransport(handler)
    )
    return PncpClient(portal_base_url="https://mockportal.test", portal_client=http)


def _itens_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/itens"):
        return httpx.Response(
            200,
            json=[
                {"numeroItem": 1, "descricao": "Recapeamento", "temResultado": True},
                {"numeroItem": 2, "descricao": "Sinalizacao", "temResultado": False},
            ],
        )
    assert url.endswith("/itens/1/resultados")  # item 2 nunca deve ser consultado
    return httpx.Response(
        200,
        json=[
            {
                "sequencialResultado": 1,
                "niFornecedor": "11222333000144",
                "nomeRazaoSocialFornecedor": "Construtora Alfa LTDA",
                "valorTotalHomologado": 1450000.0,
                "dataResultado": "2026-07-10",
                "situacaoCompraItemResultadoNome": "Informado",
            }
        ],
    )


@pytest.mark.asyncio
async def test_ingest_resultados_grava_e_e_idempotente(db_session: AsyncSession) -> None:
    lic = await _make_licitacao(db_session)
    await db_session.commit()

    client = _pncp_portal_client(_itens_handler)
    summary = await ingest_resultados(db_session, client)
    assert summary.licitacoes_processadas == 1
    assert summary.com_resultado == 1
    assert summary.resultados_gravados == 1
    assert summary.falhas == 0

    # segunda rodada: mesmo payload nao duplica
    client2 = _pncp_portal_client(_itens_handler)
    summary2 = await ingest_resultados(db_session, client2)
    assert summary2.resultados_gravados == 0

    rows = (await db_session.execute(select(ResultadoLicitacao))).scalars().all()
    assert len(rows) == 1
    assert rows[0].licitacao_id == lic.id
    assert rows[0].valor_homologado == Decimal("1450000.00")
    await client.aclose()
    await client2.aclose()


@pytest.mark.asyncio
async def test_ingest_resultados_isola_falha_por_licitacao(db_session: AsyncSession) -> None:
    await _make_licitacao(db_session)
    await _make_licitacao(
        db_session,
        external_id="99888777000166-2024-9",
        orgao_cnpj="99888777000166",
        ano_compra=2024,
        sequencial_compra=9,
    )
    await db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/99888777000166/" in url:
            return httpx.Response(500)
        return _itens_handler(request)

    client = _pncp_portal_client(handler)
    summary = await ingest_resultados(db_session, client)
    assert summary.licitacoes_processadas == 2
    assert summary.falhas == 1
    assert summary.resultados_gravados == 1
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_licitacoes_resultados.py -v -k ingest_resultados`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.modules.licitacoes.resultados'`

- [ ] **Step 3: Add schema**

Em `apps/api/app/modules/licitacoes/schemas.py`, ao final:

```python
class ResultadoIngestSummary(BaseModel):
    licitacoes_processadas: int
    com_resultado: int
    resultados_gravados: int
    falhas: int
```

- [ ] **Step 4: Implement the service**

Criar `apps/api/app/modules/licitacoes/resultados.py`:

```python
"""Squad 3: ingestao de resultados homologados a partir do PNCP.

Para cada licitacao ja captada (janela recente), consulta os itens na
API portal; itens com `temResultado=True` tem seus resultados baixados
e upsertados em `licitacoes_resultados`. Falha em uma licitacao nao
aborta as demais (mesmo espirito do ingest de publicacoes).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.models import Licitacao, ResultadoLicitacao
from app.modules.licitacoes.schemas import ResultadoIngestSummary
from app.modules.licitacoes.service import _parse_dt

logger = logging.getLogger(__name__)


def _as_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def ingest_resultados(
    db: AsyncSession,
    client: PncpClient,
    *,
    dias: int = 30,
    uf: str | None = None,
    max_licitacoes: int = 200,
) -> ResultadoIngestSummary:
    cutoff = datetime.now(UTC) - timedelta(days=dias)
    stmt = (
        select(Licitacao)
        .where(Licitacao.data_publicacao_pncp >= cutoff)
        .where(Licitacao.orgao_cnpj.is_not(None))
        .where(Licitacao.ano_compra.is_not(None))
        .where(Licitacao.sequencial_compra.is_not(None))
    )
    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
    stmt = stmt.order_by(Licitacao.data_publicacao_pncp.desc()).limit(max_licitacoes)
    licitacoes = list((await db.execute(stmt)).scalars().all())

    com_resultado = 0
    gravados = 0
    falhas = 0

    for lic in licitacoes:
        try:
            itens = await client.list_itens(
                cnpj=lic.orgao_cnpj, ano=lic.ano_compra, sequencial=lic.sequencial_compra
            )
            achou = False
            for item in itens:
                if not item.tem_resultado:
                    continue
                resultados = await client.list_item_resultados(
                    cnpj=lic.orgao_cnpj,
                    ano=lic.ano_compra,
                    sequencial=lic.sequencial_compra,
                    numero_item=item.numero_item,
                )
                for res in resultados:
                    achou = True
                    gravados += await _upsert_resultado(db, lic.id, item.numero_item, res)
            if achou:
                com_resultado += 1
        except (httpx.HTTPError, RetryError) as exc:
            logger.warning(
                "resultados: licitacao %s falhou: %s", lic.external_id, exc, exc_info=False
            )
            falhas += 1

    await db.commit()
    return ResultadoIngestSummary(
        licitacoes_processadas=len(licitacoes),
        com_resultado=com_resultado,
        resultados_gravados=gravados,
        falhas=falhas,
    )


async def _upsert_resultado(db, licitacao_id, item_numero, res) -> int:
    """Insere se (licitacao, item, sequencial) inedito; retorna 1 se gravou."""
    existing = await db.scalar(
        select(ResultadoLicitacao).where(
            ResultadoLicitacao.licitacao_id == licitacao_id,
            ResultadoLicitacao.item_numero == item_numero,
            ResultadoLicitacao.sequencial_resultado == res.sequencial_resultado,
        )
    )
    if existing is not None:
        return 0
    db.add(
        ResultadoLicitacao(
            licitacao_id=licitacao_id,
            item_numero=item_numero,
            sequencial_resultado=res.sequencial_resultado,
            cnpj_vencedor=res.ni_fornecedor,
            razao_social=res.nome_razao_social_fornecedor,
            valor_homologado=_as_decimal(res.valor_total_homologado),
            valor_unitario=_as_decimal(res.valor_unitario_homologado),
            quantidade=_as_decimal(res.quantidade_homologada),
            data_resultado=_parse_dt(res.data_resultado),
            situacao=res.situacao_nome,
            porte_fornecedor=res.porte_fornecedor_nome,
            raw=res.raw or None,
        )
    )
    await db.flush()
    return 1
```

- [ ] **Step 5: Run service tests**

Run: `uv run --extra dev pytest tests/test_licitacoes_resultados.py -v`
Expected: PASS

- [ ] **Step 6: Add the endpoint**

Em `apps/api/app/modules/licitacoes/router.py` — importar `ingest_resultados` de `app.modules.licitacoes.resultados` e `ResultadoIngestSummary` no bloco de schemas; adicionar após `ingest_endpoint`:

```python
@router.post("/ingest/resultados", response_model=ResultadoIngestSummary)
async def ingest_resultados_endpoint(
    dias: int = Query(30, ge=1, le=365),
    uf: str | None = Query(None, max_length=2),
    max_licitacoes: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    client: PncpClient = Depends(get_pncp_client),
    _: User = Depends(get_current_user),
) -> ResultadoIngestSummary:
    """Baixa resultados homologados (vencedores) das licitacoes recentes.

    Alimenta os dashboards de concorrentes e geotargeting. Disparo
    manual (sem worker), igual ao /ingest/pncp.
    """
    try:
        return await ingest_resultados(
            db, client, dias=dias, uf=uf, max_licitacoes=max_licitacoes
        )
    finally:
        await client.aclose()
```

- [ ] **Step 7: Write the router test**

```python
@pytest.mark.asyncio
async def test_ingest_resultados_endpoint_requires_auth(api_client) -> None:
    resp = await api_client.post("/api/v1/licitacoes/ingest/resultados")
    assert resp.status_code == 401
```

Run: `uv run --extra dev pytest tests/test_licitacoes_resultados.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/modules/licitacoes/resultados.py apps/api/app/modules/licitacoes/schemas.py apps/api/app/modules/licitacoes/router.py apps/api/tests/test_licitacoes_resultados.py
git commit -m "feat(módulo D): ingestao de resultados homologados do PNCP (Squad 3)"
```

---

### Task 5: Ingestão de atas de RP — service + endpoint

**Files:**
- Create: `apps/api/app/modules/licitacoes/atas.py`
- Modify: `apps/api/app/modules/licitacoes/schemas.py`
- Modify: `apps/api/app/modules/licitacoes/router.py`
- Test: `apps/api/tests/test_licitacoes_atas.py` (novo arquivo)

**Interfaces:**
- Consumes: `PncpClient.iter_atas` (Task 2), `AtaRegistroPreco` (Task 3).
- Produces: `ingest_atas(db, client, *, data_inicial, data_final) -> AtaIngestSummary`; helper `external_id_from_numero_controle(nc: str | None) -> str | None`; schema `AtaIngestSummary(total_fetched: int, gravadas: int, atualizadas: int, vinculadas: int)`; endpoint `POST /api/v1/licitacoes/ingest/atas`.

- [ ] **Step 1: Write the failing tests**

Criar `apps/api/tests/test_licitacoes_atas.py`:

```python
"""Squad 3: ingestao de atas de registro de preco (D.9)."""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.atas import external_id_from_numero_controle, ingest_atas
from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao


def test_external_id_from_numero_controle() -> None:
    assert (
        external_id_from_numero_controle("00394460000141-1-000156/2024")
        == "00394460000141-2024-156"
    )
    assert external_id_from_numero_controle(None) is None
    assert external_id_from_numero_controle("garbage") is None


def _ata_payload(numero_controle_ata: str, cancelado: bool = False) -> dict:
    return {
        "numeroControlePNCPAta": numero_controle_ata,
        "numeroAtaRegistroPreco": "001/2026",
        "anoAta": 2026,
        "numeroControlePNCPCompra": "00394460000141-1-000156/2024",
        "cancelado": cancelado,
        "vigenciaInicio": "2026-01-01",
        "vigenciaFim": "2026-12-31",
        "objetoContratacao": "RP pavimentacao",
        "cnpjOrgao": "00394460000141",
        "nomeOrgao": "Prefeitura X",
        "possibilidadeAdesao": True,
    }


def _client_returning(atas: list[dict]) -> PncpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": atas,
                "totalRegistros": len(atas),
                "totalPaginas": 1,
                "numeroPagina": 1,
                "paginasRestantes": 0,
                "empty": not atas,
            },
        )

    http = httpx.AsyncClient(base_url="https://mock.test", transport=httpx.MockTransport(handler))
    return PncpClient(base_url="https://mock.test", client=http)


@pytest.mark.asyncio
async def test_ingest_atas_upsert_e_vinculo(db_session: AsyncSession) -> None:
    lic = Licitacao(
        external_id="00394460000141-2024-156",
        source="pncp",
        ano_compra=2024,
        sequencial_compra=156,
        orgao_cnpj="00394460000141",
        uf_sigla="MG",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db_session.add(lic)
    await db_session.commit()

    client = _client_returning([_ata_payload("ata-001")])
    s1 = await ingest_atas(
        db_session, client, data_inicial=date(2026, 1, 1), data_final=date(2026, 3, 1)
    )
    assert s1.total_fetched == 1
    assert s1.gravadas == 1
    assert s1.vinculadas == 1

    ata = (await db_session.execute(select(AtaRegistroPreco))).scalar_one()
    assert ata.licitacao_id == lic.id
    assert ata.vigencia_fim == date(2026, 12, 31)

    # re-ingestao com cancelamento: atualiza a MESMA row
    client2 = _client_returning([_ata_payload("ata-001", cancelado=True)])
    s2 = await ingest_atas(
        db_session, client2, data_inicial=date(2026, 1, 1), data_final=date(2026, 3, 1)
    )
    assert s2.gravadas == 0
    assert s2.atualizadas == 1
    rows = (await db_session.execute(select(AtaRegistroPreco))).scalars().all()
    assert len(rows) == 1
    assert rows[0].cancelado is True
    await client.aclose()
    await client2.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_licitacoes_atas.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.modules.licitacoes.atas'`

- [ ] **Step 3: Add schema**

Em `schemas.py`:

```python
class AtaIngestSummary(BaseModel):
    total_fetched: int
    gravadas: int
    atualizadas: int
    vinculadas: int
```

- [ ] **Step 4: Implement the service**

Criar `apps/api/app/modules/licitacoes/atas.py`:

```python
"""Squad 3 / D.9: ingestao de atas de registro de preco do PNCP."""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpAta, PncpClient
from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao
from app.modules.licitacoes.schemas import AtaIngestSummary

# "00394460000141-1-000156/2024" -> cnpj / sequencial / ano
_RE_NUMERO_CONTROLE = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")


def external_id_from_numero_controle(nc: str | None) -> str | None:
    """Converte numeroControlePNCPCompra no external_id usado em `licitacoes`."""
    m = _RE_NUMERO_CONTROLE.match(nc or "")
    if not m:
        return None
    cnpj, seq, ano = m.groups()
    return f"{cnpj}-{int(ano)}-{int(seq)}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


async def ingest_atas(
    db: AsyncSession,
    client: PncpClient,
    *,
    data_inicial: date | str,
    data_final: date | str,
    max_paginas: int | None = None,
) -> AtaIngestSummary:
    total = gravadas = atualizadas = vinculadas = 0
    async for ata in client.iter_atas(
        data_inicial=data_inicial, data_final=data_final, max_paginas=max_paginas
    ):
        if not ata.numero_controle_pncp_ata:
            continue
        total += 1
        licitacao_id = await _find_licitacao_id(db, ata)
        if licitacao_id is not None:
            vinculadas += 1

        existing = await db.scalar(
            select(AtaRegistroPreco).where(
                AtaRegistroPreco.numero_controle_pncp_ata == ata.numero_controle_pncp_ata
            )
        )
        values = dict(
            numero_ata=ata.numero_ata,
            ano_ata=ata.ano_ata,
            licitacao_id=licitacao_id,
            orgao_cnpj=ata.cnpj_orgao,
            orgao_nome=ata.nome_orgao,
            objeto=ata.objeto_contratacao,
            vigencia_inicio=_parse_date(ata.vigencia_inicio),
            vigencia_fim=_parse_date(ata.vigencia_fim),
            cancelado=ata.cancelado,
            possibilidade_adesao=ata.possibilidade_adesao,
            raw=ata.raw or None,
        )
        if existing is None:
            db.add(
                AtaRegistroPreco(
                    numero_controle_pncp_ata=ata.numero_controle_pncp_ata, **values
                )
            )
            gravadas += 1
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            atualizadas += 1

    await db.commit()
    return AtaIngestSummary(
        total_fetched=total,
        gravadas=gravadas,
        atualizadas=atualizadas,
        vinculadas=vinculadas,
    )


async def _find_licitacao_id(db: AsyncSession, ata: PncpAta) -> int | None:
    external_id = external_id_from_numero_controle(ata.numero_controle_pncp_compra)
    if external_id is None:
        return None
    return await db.scalar(
        select(Licitacao.id).where(Licitacao.external_id == external_id)
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run --extra dev pytest tests/test_licitacoes_atas.py -v`
Expected: PASS

- [ ] **Step 6: Add the endpoint + auth test**

No `router.py` (import de `ingest_atas` + `AtaIngestSummary`), após `ingest_resultados_endpoint`:

```python
@router.post("/ingest/atas", response_model=AtaIngestSummary)
async def ingest_atas_endpoint(
    data_inicial: Annotated[date | None, Query(description="Default: 90 dias atras")] = None,
    data_final: Annotated[date | None, Query(description="Default: hoje")] = None,
    max_paginas: int | None = Query(None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    client: PncpClient = Depends(get_pncp_client),
    _: User = Depends(get_current_user),
) -> AtaIngestSummary:
    """Ingestao de atas de RP vigentes no periodo (D.9 / adesoes)."""
    today = date.today()
    data_final = data_final or today
    data_inicial = data_inicial or (data_final - timedelta(days=90))
    if data_inicial > data_final:
        raise HTTPException(status_code=400, detail="data_inicial > data_final")
    try:
        return await ingest_atas(
            db, client, data_inicial=data_inicial, data_final=data_final,
            max_paginas=max_paginas,
        )
    finally:
        await client.aclose()
```

Teste em `test_licitacoes_atas.py`:

```python
@pytest.mark.asyncio
async def test_ingest_atas_endpoint_requires_auth(api_client) -> None:
    resp = await api_client.post("/api/v1/licitacoes/ingest/atas")
    assert resp.status_code == 401
```

Run: `uv run --extra dev pytest tests/test_licitacoes_atas.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/atas.py apps/api/app/modules/licitacoes/schemas.py apps/api/app/modules/licitacoes/router.py apps/api/tests/test_licitacoes_atas.py
git commit -m "feat(módulo D): ingestao de atas de RP do PNCP (D.9, Squad 3)"
```

---

### Task 6: Dashboards concorrentes + geotargeting — service, schemas, endpoints

**Files:**
- Create: `apps/api/app/modules/licitacoes/dashboards.py`
- Modify: `apps/api/app/modules/licitacoes/schemas.py`
- Modify: `apps/api/app/modules/licitacoes/router.py`
- Test: `apps/api/tests/test_licitacoes_dashboards.py` (novo arquivo)

**Interfaces:**
- Consumes: `ResultadoLicitacao`, `Licitacao`.
- Produces: `dashboard_concorrentes(db, *, uf=None, data_inicial=None, data_final=None, limit=50) -> list[ConcorrenteRow]`; `dashboard_geotargeting(db, *, uf="MG", limit=100) -> list[GeotargetingRow]`; endpoints `GET /api/v1/licitacoes/dashboards/concorrentes` e `GET /api/v1/licitacoes/dashboards/geotargeting`.

- [ ] **Step 1: Write the failing tests**

Criar `apps/api/tests/test_licitacoes_dashboards.py`:

```python
"""Squad 3: dashboards comerciais (concorrentes, geotargeting)."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.dashboards import (
    dashboard_concorrentes,
    dashboard_geotargeting,
)
from app.modules.licitacoes.models import Licitacao, ResultadoLicitacao


async def _seed(db: AsyncSession) -> None:
    lics = [
        Licitacao(
            external_id=f"111-{2026}-{i}",
            source="pncp",
            orgao_cnpj=f"0039446000014{i}",
            orgao_razao_social=f"Prefeitura {i}",
            uf_sigla="MG",
            municipio_nome="Belo Horizonte" if i == 1 else "Uberlandia",
            modalidade_nome="Concorrencia Eletronica",
            data_publicacao_pncp=datetime(2026, 7, i, tzinfo=UTC),
        )
        for i in (1, 2)
    ]
    db.add_all(lics)
    await db.flush()
    db.add_all(
        [
            ResultadoLicitacao(
                licitacao_id=lics[0].id,
                item_numero=1,
                sequencial_resultado=1,
                cnpj_vencedor="11222333000144",
                razao_social="Construtora Alfa LTDA",
                valor_homologado=Decimal("1000000.00"),
                data_resultado=datetime(2026, 7, 10, tzinfo=UTC),
            ),
            ResultadoLicitacao(
                licitacao_id=lics[1].id,
                item_numero=1,
                sequencial_resultado=1,
                cnpj_vencedor="11222333000144",
                razao_social="Construtora Alfa LTDA",
                valor_homologado=Decimal("500000.00"),
                data_resultado=datetime(2026, 7, 12, tzinfo=UTC),
            ),
            ResultadoLicitacao(
                licitacao_id=lics[1].id,
                item_numero=2,
                sequencial_resultado=1,
                cnpj_vencedor="55666777000188",
                razao_social="Construtora Beta LTDA",
                valor_homologado=Decimal("200000.00"),
                data_resultado=datetime(2026, 7, 12, tzinfo=UTC),
            ),
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_dashboard_concorrentes_agrega_por_cnpj(db_session: AsyncSession) -> None:
    await _seed(db_session)
    rows = await dashboard_concorrentes(db_session)
    assert len(rows) == 2
    alfa = rows[0]  # ordenado por valor desc
    assert alfa.cnpj == "11222333000144"
    assert alfa.razao_social == "Construtora Alfa LTDA"
    assert alfa.licitacoes_vencidas == 2
    assert alfa.valor_total_homologado == Decimal("1500000.00")
    assert alfa.orgaos_distintos == 2


@pytest.mark.asyncio
async def test_dashboard_geotargeting_agrupa_municipio(db_session: AsyncSession) -> None:
    await _seed(db_session)
    rows = await dashboard_geotargeting(db_session, uf="MG")
    por_municipio = {r.municipio: r for r in rows}
    assert por_municipio["Uberlandia"].valor_total_homologado == Decimal("700000.00")
    assert por_municipio["Belo Horizonte"].licitacoes_com_resultado == 1


@pytest.mark.asyncio
async def test_dashboards_endpoints_respondem(api_client, db_session) -> None:
    await _seed(db_session)
    r1 = await api_client.get("/api/v1/licitacoes/dashboards/concorrentes")
    assert r1.status_code == 200
    assert r1.json()[0]["cnpj"] == "11222333000144"
    r2 = await api_client.get("/api/v1/licitacoes/dashboards/geotargeting?uf=MG")
    assert r2.status_code == 200
    assert len(r2.json()) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_licitacoes_dashboards.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.modules.licitacoes.dashboards'`

- [ ] **Step 3: Add schemas**

Em `schemas.py`:

```python
class ConcorrenteRow(BaseModel):
    cnpj: str
    razao_social: str | None
    licitacoes_vencidas: int
    valor_total_homologado: Decimal | None
    orgaos_distintos: int
    ultima_vitoria: datetime | None


class GeotargetingRow(BaseModel):
    uf: str | None
    municipio: str | None
    licitacoes_com_resultado: int
    valor_total_homologado: Decimal | None
```

- [ ] **Step 4: Implement**

Criar `apps/api/app/modules/licitacoes/dashboards.py`:

```python
"""Squad 3: dashboards comerciais (secao 9 do Projeto Tecnico do Captador).

Agregacoes SQL diretas -- sem BI externo. Os dashboards "nao captados"
e "eficiencia" (que dependem do workflow de triagem das Squads 1/2)
vivem tambem aqui; ver Task 7 do plano.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import Licitacao, ResultadoLicitacao
from app.modules.licitacoes.schemas import ConcorrenteRow, GeotargetingRow


async def dashboard_concorrentes(
    db: AsyncSession,
    *,
    uf: str | None = None,
    data_inicial: datetime | None = None,
    data_final: datetime | None = None,
    limit: int = 50,
) -> list[ConcorrenteRow]:
    valor_total = func.sum(ResultadoLicitacao.valor_homologado)
    stmt = (
        select(
            ResultadoLicitacao.cnpj_vencedor.label("cnpj"),
            func.max(ResultadoLicitacao.razao_social).label("razao_social"),
            func.count(func.distinct(ResultadoLicitacao.licitacao_id)).label(
                "licitacoes_vencidas"
            ),
            valor_total.label("valor_total_homologado"),
            func.count(func.distinct(Licitacao.orgao_cnpj)).label("orgaos_distintos"),
            func.max(ResultadoLicitacao.data_resultado).label("ultima_vitoria"),
        )
        .join(Licitacao, Licitacao.id == ResultadoLicitacao.licitacao_id)
        .where(ResultadoLicitacao.cnpj_vencedor.is_not(None))
    )
    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
    if data_inicial:
        stmt = stmt.where(ResultadoLicitacao.data_resultado >= data_inicial)
    if data_final:
        stmt = stmt.where(ResultadoLicitacao.data_resultado <= data_final)
    stmt = (
        stmt.group_by(ResultadoLicitacao.cnpj_vencedor)
        .order_by(valor_total.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [ConcorrenteRow(**row._mapping) for row in rows]


async def dashboard_geotargeting(
    db: AsyncSession,
    *,
    uf: str | None = "MG",
    limit: int = 100,
) -> list[GeotargetingRow]:
    valor_total = func.sum(ResultadoLicitacao.valor_homologado)
    stmt = (
        select(
            Licitacao.uf_sigla.label("uf"),
            Licitacao.municipio_nome.label("municipio"),
            func.count(func.distinct(ResultadoLicitacao.licitacao_id)).label(
                "licitacoes_com_resultado"
            ),
            valor_total.label("valor_total_homologado"),
        )
        .join(Licitacao, Licitacao.id == ResultadoLicitacao.licitacao_id)
    )
    if uf:
        stmt = stmt.where(Licitacao.uf_sigla == uf.upper())
    stmt = (
        stmt.group_by(Licitacao.uf_sigla, Licitacao.municipio_nome)
        .order_by(valor_total.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [GeotargetingRow(**row._mapping) for row in rows]
```

- [ ] **Step 5: Add endpoints**

No `router.py` (imports: `dashboard_concorrentes`, `dashboard_geotargeting`, `ConcorrenteRow`, `GeotargetingRow`, e `from datetime import datetime`), antes da rota `GET /{licitacao_id}` para manter as rotas literais agrupadas:

```python
# --- Squad 3: dashboards comerciais ---


@router.get("/dashboards/concorrentes", response_model=list[ConcorrenteRow])
async def dashboard_concorrentes_endpoint(
    uf: str | None = Query(None, max_length=2),
    data_inicial: datetime | None = Query(None),
    data_final: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[ConcorrenteRow]:
    return await dashboard_concorrentes(
        db, uf=uf, data_inicial=data_inicial, data_final=data_final, limit=limit
    )


@router.get("/dashboards/geotargeting", response_model=list[GeotargetingRow])
async def dashboard_geotargeting_endpoint(
    uf: str | None = Query("MG", max_length=2),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[GeotargetingRow]:
    return await dashboard_geotargeting(db, uf=uf, limit=limit)
```

- [ ] **Step 6: Run tests + lint**

Run: `uv run --extra dev pytest tests/test_licitacoes_dashboards.py -v && uv run --extra dev ruff check .`
Expected: PASS / limpo

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/dashboards.py apps/api/app/modules/licitacoes/schemas.py apps/api/app/modules/licitacoes/router.py apps/api/tests/test_licitacoes_dashboards.py
git commit -m "feat(módulo D): dashboards de concorrentes + geotargeting (Squad 3)"
```

---

### Task 7: Dashboards não-captados + eficiência — GATED nas Squads 1/2

> **Pré-condição:** Squad 1 mergeada (campo `Licitacao.status_triagem: str | None` e model `DecisaoTriagem` na tabela `licitacoes_decisoes_triagem` com colunas `id`, `licitacao_id`, `decisao`, `observacao`, `usuario_email`, `created_at`) e Squad 2 mergeada (model `PlanilhaOrcamentaria`, tabela `licitacoes_planilhas_orcamentarias`, com `status_validacao`). Se ainda não mergearam, PULE esta task e registre no PR.

**Files:**
- Modify: `apps/api/app/modules/licitacoes/dashboards.py`
- Modify: `apps/api/app/modules/licitacoes/schemas.py`
- Modify: `apps/api/app/modules/licitacoes/router.py`
- Test: `apps/api/tests/test_licitacoes_dashboards.py`

**Interfaces:**
- Consumes: `Licitacao.status_triagem` (valores da máquina de status da Squad 1: `novo_captado`, `em_analise`, `aprovado`, `rejeitado`, `processando_anexos`, `completo`, `sem_planilha`, `erro_portal`, `erro_sharepoint`), `DecisaoTriagem.created_at`, `Licitacao.created_at`.
- Produces: endpoints `GET /dashboards/nao-captados` e `GET /dashboards/eficiencia`.

- [ ] **Step 1: Write the failing tests**

Adicionar em `test_licitacoes_dashboards.py` (ajustar o import de `DecisaoTriagem` para o caminho real definido pela Squad 1):

```python
from app.modules.licitacoes.models import DecisaoTriagem  # Squad 1


@pytest.mark.asyncio
async def test_dashboard_nao_captados_conta_por_status(db_session: AsyncSession) -> None:
    for i, status in enumerate(["rejeitado", "rejeitado", "sem_planilha", "erro_portal", "completo"]):
        db_session.add(
            Licitacao(
                external_id=f"nc-{i}",
                source="pncp",
                uf_sigla="MG",
                status_triagem=status,
                data_publicacao_pncp=datetime(2026, 7, 1 + i, tzinfo=UTC),
            )
        )
    await db_session.commit()

    resp = await dashboard_nao_captados(db_session)
    assert resp.por_status["rejeitado"] == 2
    assert resp.por_status["sem_planilha"] == 1
    assert resp.por_status["erro_portal"] == 1
    assert "completo" not in resp.por_status
    assert resp.total == 4


@pytest.mark.asyncio
async def test_dashboard_eficiencia(db_session: AsyncSession) -> None:
    lic = Licitacao(
        external_id="ef-1", source="pncp", uf_sigla="MG", status_triagem="completo",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    lic2 = Licitacao(
        external_id="ef-2", source="pncp", uf_sigla="MG", status_triagem="sem_planilha",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db_session.add_all([lic, lic2])
    await db_session.flush()
    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id, decisao="aprovado", usuario_email="ana@primor.com"
        )
    )
    await db_session.commit()

    resp = await dashboard_eficiencia(db_session)
    assert resp.total_triadas == 1
    assert resp.pct_com_planilha == 50.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_licitacoes_dashboards.py -v -k "nao_captados or eficiencia"`
Expected: FAIL com `ImportError` (`dashboard_nao_captados` não existe)

- [ ] **Step 3: Add schemas**

```python
class NaoCaptadosResponse(BaseModel):
    total: int
    por_status: dict[str, int]


class EficienciaResponse(BaseModel):
    total_triadas: int
    tempo_medio_triagem_horas: float | None
    pct_com_planilha: float | None
    falhas_por_status: dict[str, int]
```

- [ ] **Step 4: Implement in `dashboards.py`**

```python
NAO_CAPTADO_STATUSES = ("rejeitado", "sem_planilha", "erro_portal", "erro_sharepoint")


async def dashboard_nao_captados(db: AsyncSession) -> NaoCaptadosResponse:
    stmt = (
        select(Licitacao.status_triagem, func.count(Licitacao.id))
        .where(Licitacao.status_triagem.in_(NAO_CAPTADO_STATUSES))
        .group_by(Licitacao.status_triagem)
    )
    rows = (await db.execute(stmt)).all()
    por_status = {status: count for status, count in rows}
    return NaoCaptadosResponse(total=sum(por_status.values()), por_status=por_status)


async def dashboard_eficiencia(db: AsyncSession) -> EficienciaResponse:
    from app.modules.licitacoes.models import DecisaoTriagem  # Squad 1

    total_triadas = (
        await db.execute(select(func.count(func.distinct(DecisaoTriagem.licitacao_id))))
    ).scalar_one()

    # tempo medio captacao -> primeira decisao (em horas)
    tempos_stmt = (
        select(
            func.min(DecisaoTriagem.created_at).label("decidido_em"),
            Licitacao.created_at.label("captado_em"),
        )
        .join(Licitacao, Licitacao.id == DecisaoTriagem.licitacao_id)
        .group_by(DecisaoTriagem.licitacao_id, Licitacao.created_at)
    )
    pares = (await db.execute(tempos_stmt)).all()
    deltas = [
        (row.decidido_em - row.captado_em).total_seconds() / 3600
        for row in pares
        if row.decidido_em and row.captado_em
    ]
    tempo_medio = round(sum(deltas) / len(deltas), 2) if deltas else None

    completo = (
        await db.execute(
            select(func.count()).where(Licitacao.status_triagem == "completo")
        )
    ).scalar_one()
    sem_planilha = (
        await db.execute(
            select(func.count()).where(Licitacao.status_triagem == "sem_planilha")
        )
    ).scalar_one()
    processadas = completo + sem_planilha
    pct = round(100.0 * completo / processadas, 2) if processadas else None

    falhas_stmt = (
        select(Licitacao.status_triagem, func.count(Licitacao.id))
        .where(Licitacao.status_triagem.in_(("erro_portal", "erro_sharepoint")))
        .group_by(Licitacao.status_triagem)
    )
    falhas = {status: count for status, count in (await db.execute(falhas_stmt)).all()}

    return EficienciaResponse(
        total_triadas=total_triadas,
        tempo_medio_triagem_horas=tempo_medio,
        pct_com_planilha=pct,
        falhas_por_status=falhas,
    )
```

Endpoints no `router.py`, junto aos outros dashboards:

```python
@router.get("/dashboards/nao-captados", response_model=NaoCaptadosResponse)
async def dashboard_nao_captados_endpoint(
    db: AsyncSession = Depends(get_db),
) -> NaoCaptadosResponse:
    return await dashboard_nao_captados(db)


@router.get("/dashboards/eficiencia", response_model=EficienciaResponse)
async def dashboard_eficiencia_endpoint(
    db: AsyncSession = Depends(get_db),
) -> EficienciaResponse:
    return await dashboard_eficiencia(db)
```

- [ ] **Step 5: Run tests + lint**

Run: `uv run --extra dev pytest tests/test_licitacoes_dashboards.py -v && uv run --extra dev ruff check .`
Expected: PASS / limpo

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/modules/licitacoes/dashboards.py apps/api/app/modules/licitacoes/schemas.py apps/api/app/modules/licitacoes/router.py apps/api/tests/test_licitacoes_dashboards.py
git commit -m "feat(módulo D): dashboards nao-captados + eficiencia (Squad 3, pos Squads 1/2)"
```

---

### Task 8: UI — página de dashboards comerciais no web

**Files:**
- Create: `apps/web/src/app/(dashboard)/licitacoes/dashboards/page.tsx`
- Modify: `apps/web/src/app/(dashboard)/licitacoes/page.tsx` (link no header)

**Interfaces:**
- Consumes: `apiFetch` de `@/lib/api`; endpoints das Tasks 6–7. Os dois endpoints gated (nao-captados/eficiencia) podem não existir ainda — o fetch é tolerante (try/catch → seção some).

- [ ] **Step 1: Create the page**

Criar `apps/web/src/app/(dashboard)/licitacoes/dashboards/page.tsx`:

```tsx
import Link from "next/link";

import { apiFetch } from "@/lib/api";

type ConcorrenteRow = {
  cnpj: string;
  razao_social: string | null;
  licitacoes_vencidas: number;
  valor_total_homologado: string | null;
  orgaos_distintos: number;
  ultima_vitoria: string | null;
};

type GeotargetingRow = {
  uf: string | null;
  municipio: string | null;
  licitacoes_com_resultado: number;
  valor_total_homologado: string | null;
};

type NaoCaptados = { total: number; por_status: Record<string, number> };

type Eficiencia = {
  total_triadas: number;
  tempo_medio_triagem_horas: number | null;
  pct_com_planilha: number | null;
  falhas_por_status: Record<string, number>;
};

export const dynamic = "force-dynamic";

async function safeFetch<T>(path: string): Promise<T | null> {
  try {
    return await apiFetch<T>(path);
  } catch {
    return null;
  }
}

function brl(value: string | null): string {
  if (!value) return "—";
  const num = Number(value);
  if (Number.isNaN(num)) return value;
  return num.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

const STATUS_LABELS: Record<string, string> = {
  rejeitado: "Rejeitados",
  sem_planilha: "Sem planilha",
  erro_portal: "Erro de portal",
  erro_sharepoint: "Erro SharePoint",
};

export default async function DashboardsComerciaisPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const uf = typeof searchParams.uf === "string" && searchParams.uf ? searchParams.uf : "MG";

  const [concorrentes, geo, naoCaptados, eficiencia] = await Promise.all([
    safeFetch<ConcorrenteRow[]>(`/api/v1/licitacoes/dashboards/concorrentes?uf=${uf}`),
    safeFetch<GeotargetingRow[]>(`/api/v1/licitacoes/dashboards/geotargeting?uf=${uf}`),
    safeFetch<NaoCaptados>("/api/v1/licitacoes/dashboards/nao-captados"),
    safeFetch<Eficiencia>("/api/v1/licitacoes/dashboards/eficiencia"),
  ]);

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Inteligência comercial</h1>
          <p className="mt-1 text-sm text-slate-500">
            Concorrentes, geotargeting e eficiência da esteira — dados do PNCP.
          </p>
        </div>
        <Link
          href="/licitacoes"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Licitações
        </Link>
      </header>

      <form className="flex items-end gap-3 rounded-xl border border-slate-200 bg-white p-4">
        <label className="flex flex-col text-xs font-medium text-slate-600">
          UF
          <input
            name="uf"
            defaultValue={uf}
            maxLength={2}
            className="mt-1 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm uppercase"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
        >
          Filtrar
        </button>
      </form>

      {eficiencia && (
        <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">Licitações triadas</p>
            <p className="mt-1 text-2xl font-bold">{eficiencia.total_triadas}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">Tempo médio até triagem</p>
            <p className="mt-1 text-2xl font-bold">
              {eficiencia.tempo_medio_triagem_horas != null
                ? `${eficiencia.tempo_medio_triagem_horas}h`
                : "—"}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">% com planilha localizada</p>
            <p className="mt-1 text-2xl font-bold">
              {eficiencia.pct_com_planilha != null ? `${eficiencia.pct_com_planilha}%` : "—"}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">Falhas de portal/SharePoint</p>
            <p className="mt-1 text-2xl font-bold">
              {Object.values(eficiencia.falhas_por_status).reduce((a, b) => a + b, 0)}
            </p>
          </div>
        </section>
      )}

      {naoCaptados && naoCaptados.total > 0 && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4">
          <h2 className="text-sm font-semibold text-red-800">
            Não captados ({naoCaptados.total})
          </h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(naoCaptados.por_status).map(([status, count]) => (
              <span
                key={status}
                className="rounded-full bg-white px-3 py-1 text-xs font-medium text-red-700"
              >
                {STATUS_LABELS[status] ?? status}: {count}
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold">
          Concorrentes por CNPJ ({uf})
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
              <th className="px-4 py-2">Fornecedor</th>
              <th className="px-4 py-2">CNPJ</th>
              <th className="px-4 py-2 text-right">Vitórias</th>
              <th className="px-4 py-2 text-right">Valor homologado</th>
              <th className="px-4 py-2 text-right">Órgãos</th>
            </tr>
          </thead>
          <tbody>
            {(concorrentes ?? []).map((c) => (
              <tr key={c.cnpj} className="border-b border-slate-50">
                <td className="px-4 py-2 font-medium">{c.razao_social ?? "—"}</td>
                <td className="px-4 py-2 text-slate-500">{c.cnpj}</td>
                <td className="px-4 py-2 text-right">{c.licitacoes_vencidas}</td>
                <td className="px-4 py-2 text-right">{brl(c.valor_total_homologado)}</td>
                <td className="px-4 py-2 text-right">{c.orgaos_distintos}</td>
              </tr>
            ))}
            {(!concorrentes || concorrentes.length === 0) && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
                  Sem resultados ingeridos — rode POST /licitacoes/ingest/resultados.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold">
          Geotargeting — verba homologada por município ({uf})
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
              <th className="px-4 py-2">Município</th>
              <th className="px-4 py-2 text-right">Licitações c/ resultado</th>
              <th className="px-4 py-2 text-right">Valor homologado</th>
            </tr>
          </thead>
          <tbody>
            {(geo ?? []).map((g) => (
              <tr key={`${g.uf}-${g.municipio}`} className="border-b border-slate-50">
                <td className="px-4 py-2 font-medium">
                  {g.municipio ?? "—"} <span className="text-slate-400">{g.uf}</span>
                </td>
                <td className="px-4 py-2 text-right">{g.licitacoes_com_resultado}</td>
                <td className="px-4 py-2 text-right">{brl(g.valor_total_homologado)}</td>
              </tr>
            ))}
            {(!geo || geo.length === 0) && (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-slate-400">
                  Sem dados para {uf}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Add the link on the licitacoes page header**

Em `apps/web/src/app/(dashboard)/licitacoes/page.tsx`, dentro do `<div className="flex items-center gap-2">` do header (antes do link de Certidões):

```tsx
          <Link
            href="/licitacoes/dashboards"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Inteligência comercial →
          </Link>
```

- [ ] **Step 3: Verify build + lint**

Run (de `apps/web`): `npm run lint && npx tsc --noEmit`
Expected: limpo

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/\(dashboard\)/licitacoes/dashboards/page.tsx apps/web/src/app/\(dashboard\)/licitacoes/page.tsx
git commit -m "feat(ui): pagina de inteligencia comercial de licitacoes (Squad 3)"
```

---

### Task 9: Suite completa + roadmap

**Files:**
- Modify: `docs/roadmap.md`

- [ ] **Step 1: Run everything**

De `apps/api`: `uv run --extra dev pytest && uv run --extra dev ruff check .`
De `apps/web`: `npm run lint && npx tsc --noEmit`
Expected: tudo verde. Qualquer teste pré-existente quebrado por estas mudanças é bug NOSSO — corrigir antes de seguir.

- [ ] **Step 2: Update roadmap**

Em `docs/roadmap.md`, linha do Módulo D: no trecho **Próximos passos**, remover `adesões/atas de RP (D.9)` e acrescentar ao corpo da entrega: `ingestão de resultados/homologações + atas RP com dashboards de concorrentes/geotargeting (✓ D.9 / Squad 3)`.

- [ ] **Step 3: Commit**

```bash
git add docs/roadmap.md
git commit -m "docs(roadmap): D.9 + dashboards comerciais entregues (Squad 3)"
```

---

## Self-Review (executada na escrita do plano)

1. **Cobertura do escopo:** ingestão de resultados (Tasks 1, 3, 4) ✓; atas/adesões D.9 (Tasks 2, 3, 5) ✓; dashboards concorrentes/geotargeting (Task 6) ✓; não-captados/eficiência (Task 7, gated) ✓; UI (Task 8) ✓; migration (Task 3) ✓.
2. **Placeholders:** nenhum "TBD/TODO"; todo step de código tem o código.
3. **Consistência de tipos:** `ResultadoLicitacao`/`AtaRegistroPreco` usados nas Tasks 4–7 conferem com a definição da Task 3; `PncpItem.tem_resultado`/`list_item_resultados` conferem entre Tasks 1 e 4; `ConcorrenteRow.cnpj` etc. conferem entre Task 6 (schema/service) e Task 8 (types TS).

## Decisões e riscos registrados

- **Unique constraint de resultados**: o pedido original era `(licitacao_id, item_numero)`, mas a API do PNCP retorna múltiplos resultados por item (`sequencialResultado` — ordem de classificação em SRP). Constraint final: `(licitacao_id, item_numero, sequencial_resultado)`. Sem isso a ingestão real quebraria em SRP.
- **`valor` da ata é NULL**: `AtaRegistroPrecoPeriodoDTO` (verificado no OpenAPI live) não tem campo de valor; coluna mantida para preenchimento futuro via licitação vinculada.
- **Sem Celery**: ingestões são endpoints manuais (padrão do módulo e compatível com a demo Vercel); um beat pode ser adicionado quando houver worker.
- **Task 7 gated**: nomes exatos consumidos das Squads 1/2 estão declarados; se os nomes divergirem no merge real, ajustar imports/campos na Task 7 (única task acoplada).
- **`down_revision`**: escrito contra o head `b8c9d0e1f2a3`; recalcular no momento da implementação (squads paralelas).
