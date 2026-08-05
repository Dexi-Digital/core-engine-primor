# Captador Squad 2 — Processamento Pós-Aprovação (Pasta, Planilha Orçamentária, Aba de Triagem) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando a analista aprova um edital, o sistema baixa os anexos, cria a pasta do projeto no storage, identifica a planilha orçamentária editável (XLSX/XLS/ODS) e entrega tudo na Aba de Triagem com hiperlinks diretos — status `sem_planilha` com alerta vermelho quando nenhuma planilha compatível é encontrada.

**Architecture:** Extensão do módulo `apps/api/app/modules/licitacoes/` existente. O pipeline de download D.4 (`editais.py`) é reutilizado intacto; um novo serviço `processamento.py` orquestra a máquina de status pós-aprovação; a identificação de planilha é uma lib pura (`identificacao_planilha.py`) com scoring por extensão + nome + conteúdo (seção 8 do Projeto Técnico do cliente); a pasta do projeto usa a abstração `EditaisStorage` já existente (LocalStorage hoje, OneDrive/Graph com credenciais, SharePoint corporativo = troca de config futura). Execução via Celery (`worker.tasks.licitacoes`) **e** endpoint manual (a demo Vercel não tem worker).

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic, Celery, openpyxl + odfpy (novas deps), Next.js App Router (apps/web), pytest + aiosqlite.

## Global Constraints

- Padrão do repo: reads sem auth; mutações exigem `_: User = Depends(get_current_user)` (audit/LGPD).
- Testes rodam com SQLite in-memory via `Base.metadata.create_all` (fixtures em `apps/api/tests/conftest.py`) — modelos novos ficam disponíveis automaticamente; migrations Alembic só rodam em Postgres real.
- Lint/teste: `cd apps/api && uv run ruff check . && uv run --extra dev pytest`; web: `cd apps/web && npm run lint && npm run build`.
- Novas dependências python entram em `apps/api/pyproject.toml` **e** espelhadas em `apps/api/requirements.txt` (builder da Vercel lê requirements.txt).
- Docstrings/comентários em pt-BR sem acento nos módulos onde já é assim (seguir o arquivo tocado).
- Idempotência: reprocessar a mesma licitação não pode duplicar linhas (mesmo princípio do D.4).
- **Migração paralela entre squads:** `down_revision` da migration deste plano deve apontar para o head vigente NO MOMENTO da implementação. Head no momento da escrita: `b8c9d0e1f2a3`. Verifique com: `cd apps/api && uv run --extra dev alembic heads`.

## Interfaces com a Squad 1 (contrato — pré-requisito das Tasks 5–8)

A Squad 1 (Tela de Captação) entrega ANTES das Tasks 5–8 deste plano:

1. Coluna `Licitacao.status_triagem: Mapped[str]` em `app/modules/licitacoes/models.py` — `String(32)`, `default="novo_captado"`, `server_default="novo_captado"`, `index=True`. Valores: `novo_captado`, `em_analise`, `aprovado`, `rejeitado`, `processando_anexos`, `completo`, `sem_planilha`, `erro_portal`, `erro_sharepoint`.
2. Model `DecisaoTriagem` em `app/modules/licitacoes/models.py`, tabela `licitacoes_decisoes_triagem`, colunas: `id` (pk), `licitacao_id` (FK `licitacoes.id`, CASCADE, index), `decisao: Mapped[str]` (`String(16)`), `observacao: Mapped[str | None]` (`Text`), `usuario_email: Mapped[str]` (`String(255)`), `created_at` (server_default `func.now()`).
3. Ao aprovar, a Squad 1 seta `status_triagem = "aprovado"` e despacha `send_task("worker.tasks.licitacoes.processar_edital_aprovado", args=[licitacao_id])`.

Tasks 1–4 deste plano **não** dependem da Squad 1 e podem começar imediatamente.

**Pré-flight das Tasks 5–8** (rode antes de começar a Task 5):

```bash
grep -n "status_triagem" apps/api/app/modules/licitacoes/models.py && grep -n "class DecisaoTriagem" apps/api/app/modules/licitacoes/models.py
```

Se algum grep falhar, PARE e aguarde o merge da Squad 1 (ou rebaseie por cima da branch dela).

## Decisões de design (registradas — não rediscutir durante implementação)

- **Pasta física continua `{licitacao_id}`**: o D.4 já salva anexos em `{root}/{licitacao_id}/…` de forma idempotente. Renomear a pasta para o slug humano quebraria os re-downloads idempotentes por path. O slug `<UF>-<municipio>-<orgao>-<numero>` fica em `PastaProjeto.nome_pasta` (nome de exibição na Aba de Triagem). Quando a conta de serviço do SharePoint corporativo chegar, reavaliamos pasta nomeada por slug.
- **Link da planilha = `AnexoEdital.source_url`** (URL pública do PNCP): abre/baixa o arquivo com um clique sem problema de auth no browser — cumpre o critério de UAT hoje, inclusive na demo Vercel. `PlanilhaOrcamentaria.link` guarda essa URL; com SharePoint real passará a guardar o `webUrl` do Graph.
- **`.xls` legado**: openpyxl não lê `.xls`; pontua só por extensão + nome (sem conteúdo). Registrado como limitação aceita — a maioria dos portais publica `.xlsx`.
- **Planilha principal exige `score >= 10`** (`LIMIAR_PRINCIPAL`): pelo menos um termo no nome ou dois no conteúdo; evita marcar como orçamento qualquer xlsx aleatório (ex.: lista de presença).
- Escolha manual da analista (`status_validacao="principal_manual"`) é *sticky*: reprocessamento não a sobrescreve.

---

### Task 1: Lib de identificação da planilha orçamentária (`identificacao_planilha.py`)

**Files:**
- Modify: `apps/api/pyproject.toml` (deps `openpyxl>=3.1`, `odfpy>=1.4`)
- Modify: `apps/api/requirements.txt` (espelhar as duas deps)
- Create: `apps/api/app/modules/licitacoes/identificacao_planilha.py`
- Test: `apps/api/tests/test_licitacoes_identificacao_planilha.py`

**Interfaces:**
- Consumes: nada (lib pura, sem DB/IO).
- Produces (usado pela Task 5):
  - `EXTENSOES_ELEGIVEIS: frozenset[str]` — `{".xlsx", ".xls", ".ods"}`
  - `LIMIAR_PRINCIPAL: int = 10`
  - `normalizar(texto: str) -> str` — minúsculas, sem acento, `_`/`-` viram espaço
  - `score_nome(filename: str) -> int`
  - `score_conteudo(filename: str, data: bytes) -> int`
  - `classificar_anexo(filename: str, data: bytes | None) -> int | None` — `None` = extensão inelegível

- [ ] **Step 1: Adicionar dependências**

Em `apps/api/pyproject.toml`, na lista `dependencies` (logo após a linha `"pypdf>=5.1.0",`):

```toml
    "openpyxl>=3.1",
    "odfpy>=1.4",
```

Em `apps/api/requirements.txt` (após a linha `pypdf>=5.1.0`):

```
openpyxl>=3.1
odfpy>=1.4
```

Rode: `cd apps/api && uv sync --extra dev`

- [ ] **Step 2: Escrever os testes que falham**

Criar `apps/api/tests/test_licitacoes_identificacao_planilha.py`:

```python
"""Tests da identificacao de planilha orcamentaria (Captador Squad 2).

Cobre o scoring da secao 8 do Projeto Tecnico: extensao + nome +
conteudo interno, com fallback gracioso para bytes corrompidos.
"""
from __future__ import annotations

import io

from app.modules.licitacoes.identificacao_planilha import (
    EXTENSOES_ELEGIVEIS,
    LIMIAR_PRINCIPAL,
    classificar_anexo,
    normalizar,
    score_conteudo,
    score_nome,
)


def _xlsx_bytes(primeira_linha: list[str]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(primeira_linha)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ods_bytes(primeira_linha: list[str]) -> bytes:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Plan1")
    row = TableRow()
    for valor in primeira_linha:
        cell = TableCell()
        cell.addElement(P(text=valor))
        row.addElement(cell)
    table.addElement(row)
    doc.spreadsheet.addElement(table)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_normalizar_remove_acentos_e_separadores() -> None:
    assert normalizar("Orçamento_Sintético-2026") == "orcamento sintetico 2026"


def test_score_nome_pontua_10_por_termo() -> None:
    # "orcament" + "planilha" = 2 termos -> 20
    assert score_nome("Planilha_Orcamentaria.xlsx") == 20
    assert score_nome("edital_retificado.pdf") == 0


def test_score_conteudo_xlsx_acha_cabecalhos() -> None:
    data = _xlsx_bytes(["Item", "Unid", "Quant", "Preço Unitário", "Total"])
    # item, unid, quant, preco unitario, total -> 5 termos x 5 = 25
    assert score_conteudo("qualquer.xlsx", data) == 25


def test_score_conteudo_ods_acha_cabecalhos() -> None:
    data = _ods_bytes(["Item", "Quant", "Total"])
    assert score_conteudo("qualquer.ods", data) == 15


def test_score_conteudo_bytes_corrompidos_retorna_zero() -> None:
    assert score_conteudo("x.xlsx", b"nao sou um zip") == 0


def test_classificar_anexo_pdf_e_inelegivel() -> None:
    assert classificar_anexo("edital.pdf", b"%PDF") is None


def test_classificar_anexo_xls_legado_pontua_so_pelo_nome() -> None:
    # .xls e elegivel mas openpyxl nao le: conteudo ignorado.
    assert classificar_anexo("orcamento.xls", b"\xd0\xcf\x11\xe0lixo") == 10


def test_classificar_anexo_soma_nome_e_conteudo() -> None:
    data = _xlsx_bytes(["Item", "Total"])
    # nome: "orcament" -> 10; conteudo: item + total -> 10. Soma 20.
    assert classificar_anexo("orcamento.xlsx", data) == 20


def test_classificar_anexo_sem_bytes_usa_so_nome() -> None:
    assert classificar_anexo("planilha_de_custos.xlsx", None) == 20


def test_constantes_exportadas() -> None:
    assert ".xlsx" in EXTENSOES_ELEGIVEIS
    assert ".ods" in EXTENSOES_ELEGIVEIS
    assert ".xls" in EXTENSOES_ELEGIVEIS
    assert LIMIAR_PRINCIPAL == 10
```

Nota sobre os valores esperados: `test_score_nome` usa `Planilha_Orcamentaria.xlsx` → normalizado `planilha orcamentaria.xlsx`, contém `planilha` e `orcament` (2×10=20). `planilha_de_custos.xlsx` contém `planilha` e `custo` (20).

- [ ] **Step 3: Rodar para confirmar que falham**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_identificacao_planilha.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.licitacoes.identificacao_planilha'`

- [ ] **Step 4: Implementar a lib**

Criar `apps/api/app/modules/licitacoes/identificacao_planilha.py`:

```python
"""Identificacao da planilha orcamentaria editavel (Captador, secao 8).

Combina tres criterios para reduzir falso positivo -- a identificacao
NAO depende so do nome do arquivo, porque muitos portais publicam
nomenclatura generica:

  1. extensao: apenas XLSX / XLS / ODS sao elegiveis (PDF e arquivado,
     mas nao substitui a planilha editavel no aceite do cliente);
  2. nome do arquivo: termos do vocabulario de orcamento (10 pts cada);
  3. conteudo interno: cabecalhos tipicos de planilha de orcamento
     nas primeiras linhas (5 pts cada termo encontrado).

Funcoes puras, sem IO/DB -- quem le bytes do storage e o caller
(`processamento.py`). Bytes corrompidos pontuam 0 no conteudo em vez
de propagar excecao: um zip invalido nao pode derrubar o pipeline.
"""
from __future__ import annotations

import io
import logging
import unicodedata
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

EXTENSOES_ELEGIVEIS: frozenset[str] = frozenset({".xlsx", ".xls", ".ods"})

# Planilha principal exige score >= LIMIAR_PRINCIPAL: um termo no nome
# OU dois no conteudo. Evita eleger um xlsx qualquer (lista de presenca)
# como orcamento.
LIMIAR_PRINCIPAL: int = 10

# Vocabulario inicial do Projeto Tecnico (secao 8.1). Radicais sem
# acento -- a comparacao roda sobre texto normalizado.
TERMOS_NOME: tuple[str, ...] = (
    "orcament",
    "planilha",
    "composic",
    "cronograma",
    "bdi",
    "quantitativo",
    "preco unitario",
    "memoria de calculo",
    "custo",
    "valor total",
)

TERMOS_CONTEUDO: tuple[str, ...] = (
    "item",
    "unid",
    "quant",
    "preco unitario",
    "valor unitario",
    "bdi",
    "total",
    "descricao",
)

_PONTOS_NOME = 10
_PONTOS_CONTEUDO = 5

# Limites de varredura do conteudo: suficiente para achar o cabecalho,
# barato o bastante para rodar em serverless.
_MAX_ABAS = 3
_MAX_LINHAS = 30
_MAX_COLUNAS = 20


def normalizar(texto: str) -> str:
    """Minusculas, sem acento; `_`, `-` e quebras viram espaco unico."""
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    for sep in ("_", "-", "\n", "\t"):
        sem_acento = sem_acento.replace(sep, " ")
    return " ".join(sem_acento.split())


def score_nome(filename: str) -> int:
    nome = normalizar(filename)
    return sum(_PONTOS_NOME for termo in TERMOS_NOME if termo in nome)


def _texto_celulas_xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    pedacos: list[str] = []
    try:
        for ws in wb.worksheets[:_MAX_ABAS]:
            for row in ws.iter_rows(
                min_row=1, max_row=_MAX_LINHAS, max_col=_MAX_COLUNAS, values_only=True
            ):
                pedacos.extend(str(v) for v in row if v is not None)
    finally:
        wb.close()
    return " ".join(pedacos)


def _texto_celulas_ods(data: bytes) -> str:
    from odf import teletype
    from odf.opendocument import load as ods_load
    from odf.table import Table, TableCell, TableRow

    doc = ods_load(io.BytesIO(data))
    pedacos: list[str] = []
    for table in doc.spreadsheet.getElementsByType(Table)[:_MAX_ABAS]:
        for row in table.getElementsByType(TableRow)[:_MAX_LINHAS]:
            for cell in row.getElementsByType(TableCell)[:_MAX_COLUNAS]:
                texto = teletype.extractText(cell)
                if texto:
                    pedacos.append(texto)
    return " ".join(pedacos)


def score_conteudo(filename: str, data: bytes) -> int:
    """Pontua cabecalhos tipicos nas primeiras celulas. 0 se ilegivel."""
    ext = PurePosixPath(filename).suffix.lower()
    try:
        if ext == ".xlsx":
            texto = _texto_celulas_xlsx(data)
        elif ext == ".ods":
            texto = _texto_celulas_ods(data)
        else:
            # .xls legado: openpyxl nao le. Score de conteudo 0 --
            # decisao registrada no plano (nome + extensao bastam).
            return 0
    except Exception as exc:  # noqa: BLE001 -- qualquer corrupcao = score 0
        logger.warning("planilha ilegivel (%s): %s", filename, exc)
        return 0
    texto_norm = normalizar(texto)
    return sum(
        _PONTOS_CONTEUDO for termo in TERMOS_CONTEUDO if termo in texto_norm
    )


def classificar_anexo(filename: str, data: bytes | None) -> int | None:
    """Score total do anexo, ou `None` se a extensao e inelegivel."""
    ext = PurePosixPath(filename).suffix.lower()
    if ext not in EXTENSOES_ELEGIVEIS:
        return None
    total = score_nome(filename)
    if data is not None:
        total += score_conteudo(filename, data)
    return total
```

- [ ] **Step 5: Rodar os testes até passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_identificacao_planilha.py -v`
Expected: PASS (10 testes). Se `test_score_conteudo_xlsx_acha_cabecalhos` falhar com 20 em vez de 25, confira se "unid" está sendo achado dentro de "unidade" — a comparação é por substring, então "Unid" normalizado contém "unid"; o esperado é 25.

- [ ] **Step 6: Lint + suíte completa**

Run: `cd apps/api && uv run ruff check . && uv run --extra dev pytest -q`
Expected: ruff limpo; 565+ testes passando (nenhum quebrado).

- [ ] **Step 7: Commit**

```bash
git add apps/api/pyproject.toml apps/api/requirements.txt apps/api/uv.lock \
  apps/api/app/modules/licitacoes/identificacao_planilha.py \
  apps/api/tests/test_licitacoes_identificacao_planilha.py
git commit -m "feat(captador): lib de identificacao de planilha orcamentaria (score ext+nome+conteudo)"
```

---

### Task 2: Models `PastaProjeto` + `PlanilhaOrcamentaria`, migration e schemas Read

**Files:**
- Modify: `apps/api/app/modules/licitacoes/models.py` (append ao final)
- Modify: `apps/api/app/modules/licitacoes/schemas.py` (append ao final)
- Create: `apps/api/alembic/versions/a7b8c9d0e1f2_captador_pastas_planilhas.py`
- Test: `apps/api/tests/test_licitacoes_processamento.py` (criado aqui, cresce nas Tasks 5–6)

**Interfaces:**
- Consumes: `Base` de `app.core.db`; `Licitacao`, `AnexoEdital` (models existentes).
- Produces (usados pelas Tasks 5–7):
  - `PastaProjeto` — tabela `licitacoes_pastas_projeto`; campos `id: int`, `licitacao_id: int` (unique FK), `nome_pasta: str`, `caminho: str`, `link_pasta: str | None`, `storage_backend: str`, `status: str`, `created_at`, `updated_at`
  - `PlanilhaOrcamentaria` — tabela `licitacoes_planilhas_orcamentarias`; campos `id: int`, `licitacao_id: int` (FK, index), `anexo_id: int` (unique FK), `nome_arquivo: str`, `extensao: str`, `score_classificacao: int`, `link: str | None`, `status_validacao: str` (`"automatica" | "principal_manual" | "descartada"`), `principal: bool`, `created_at`, `updated_at`
  - Schemas pydantic: `PastaProjetoRead`, `PlanilhaOrcamentariaRead`

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/api/tests/test_licitacoes_processamento.py`:

```python
"""Tests do processamento pos-aprovacao (Captador Squad 2)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import (
    AnexoEdital,
    Edital,
    Licitacao,
    PastaProjeto,
    PlanilhaOrcamentaria,
)


async def _mk_licitacao(db: AsyncSession, **overrides) -> Licitacao:
    dados = dict(
        external_id="12345678000100-2026-7",
        source="pncp",
        numero_compra="002/2026",
        ano_compra=2026,
        sequencial_compra=7,
        objeto_compra="Pavimentacao asfaltica",
        orgao_cnpj="12345678000100",
        orgao_razao_social="Prefeitura de Teste",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
    )
    dados.update(overrides)
    lic = Licitacao(**dados)
    db.add(lic)
    await db.commit()
    await db.refresh(lic)
    return lic


@pytest.mark.asyncio
async def test_pasta_projeto_unica_por_licitacao(db_session: AsyncSession) -> None:
    lic = await _mk_licitacao(db_session)
    db_session.add(
        PastaProjeto(
            licitacao_id=lic.id,
            nome_pasta="mg-belo_horizonte-prefeitura-002_2026",
            caminho=f"/tmp/editais/{lic.id}",
            storage_backend="local",
        )
    )
    await db_session.commit()

    db_session.add(
        PastaProjeto(
            licitacao_id=lic.id,
            nome_pasta="duplicada",
            caminho="/tmp/x",
            storage_backend="local",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_planilha_orcamentaria_defaults(db_session: AsyncSession) -> None:
    lic = await _mk_licitacao(db_session)
    edital = Edital(licitacao_id=lic.id, source="pncp", status="completed")
    db_session.add(edital)
    await db_session.flush()
    anexo = AnexoEdital(
        edital_id=edital.id,
        sequencial_documento=1,
        titulo="Planilha Orcamentaria",
        source_url="https://pncp.gov.br/arquivos/1",
        filename="planilha_orcamentaria.xlsx",
        storage_path="/tmp/x.xlsx",
    )
    db_session.add(anexo)
    await db_session.flush()

    db_session.add(
        PlanilhaOrcamentaria(
            licitacao_id=lic.id,
            anexo_id=anexo.id,
            nome_arquivo="planilha_orcamentaria.xlsx",
            extensao=".xlsx",
            score_classificacao=35,
            link="https://pncp.gov.br/arquivos/1",
        )
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(PlanilhaOrcamentaria))
    ).scalar_one()
    assert row.status_validacao == "automatica"
    assert row.principal is False
```

- [ ] **Step 2: Rodar para confirmar que falha**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py -v`
Expected: FAIL — `ImportError: cannot import name 'PastaProjeto'`

- [ ] **Step 3: Implementar os models**

Append ao final de `apps/api/app/modules/licitacoes/models.py`:

```python
# --- Captador Squad 2: pasta do projeto + planilha orcamentaria -----------


class PastaProjeto(Base):
    """Pasta do projeto criada no storage apos aprovacao na triagem.

    Uma por licitacao. `caminho` e o handle fisico (path local ou path
    Graph); `link_pasta` e a URL clicavel (webUrl do OneDrive) quando o
    backend fornece uma -- no backend local fica None e a Aba de
    Triagem cai no detalhe interno da licitacao.

    Decisao registrada no plano: a pasta fisica continua sendo
    `{licitacao_id}` (idempotencia do D.4); o slug humano
    `<uf>-<municipio>-<orgao>-<numero>` fica so em `nome_pasta`.
    """

    __tablename__ = "licitacoes_pastas_projeto"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    nome_pasta: Mapped[str] = mapped_column(String(255))
    caminho: Mapped[str] = mapped_column(String(1024))
    link_pasta: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    storage_backend: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(32), default="criada", server_default="criada"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlanilhaOrcamentaria(Base):
    """Anexo elegivel (XLSX/XLS/ODS) classificado como possivel planilha
    orcamentaria. A de maior score acima do limiar vira `principal=True`;
    a analista pode trocar via PATCH (`status_validacao=principal_manual`,
    que o reprocessamento respeita).
    """

    __tablename__ = "licitacoes_planilhas_orcamentarias"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        index=True,
    )
    anexo_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes_editais_anexos.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    extensao: Mapped[str] = mapped_column(String(16))
    score_classificacao: Mapped[int] = mapped_column(Integer, default=0)
    link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # "automatica" | "principal_manual" | "descartada"
    status_validacao: Mapped[str] = mapped_column(
        String(32), default="automatica", server_default="automatica"
    )
    principal: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

(Imports `Boolean`, `DateTime`, `ForeignKey`, `Integer`, `String`, `func`, `datetime`, `Mapped`, `mapped_column`, `Base` já existem no topo do arquivo.)

- [ ] **Step 4: Rodar o teste até passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py -v`
Expected: PASS (2 testes)

- [ ] **Step 5: Schemas Read**

Append ao final de `apps/api/app/modules/licitacoes/schemas.py`:

```python
# --- Captador Squad 2 ------------------------------------------------------


class PastaProjetoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    licitacao_id: int
    nome_pasta: str
    caminho: str
    link_pasta: str | None
    storage_backend: str
    status: str


class PlanilhaOrcamentariaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    licitacao_id: int
    anexo_id: int
    nome_arquivo: str
    extensao: str
    score_classificacao: int
    link: str | None
    status_validacao: str
    principal: bool
```

- [ ] **Step 6: Migration Alembic**

Antes: `cd apps/api && uv run --extra dev alembic heads` — se o head não for `b8c9d0e1f2a3`, use o head real em `down_revision`.

Criar `apps/api/alembic/versions/a7b8c9d0e1f2_captador_pastas_planilhas.py`:

```python
"""captador: pastas de projeto + planilhas orcamentarias

Revision ID: a7b8c9d0e1f2
Revises: b8c9d0e1f2a3
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_pastas_projeto",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nome_pasta", sa.String(length=255), nullable=False),
        sa.Column("caminho", sa.String(length=1024), nullable=False),
        sa.Column("link_pasta", sa.String(length=1024), nullable=True),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="criada"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_licitacoes_pastas_projeto_licitacao_id",
        "licitacoes_pastas_projeto",
        ["licitacao_id"],
        unique=True,
    )

    op.create_table(
        "licitacoes_planilhas_orcamentarias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "anexo_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes_editais_anexos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nome_arquivo", sa.String(length=255), nullable=False),
        sa.Column("extensao", sa.String(length=16), nullable=False),
        sa.Column(
            "score_classificacao", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("link", sa.String(length=1024), nullable=True),
        sa.Column(
            "status_validacao",
            sa.String(length=32),
            nullable=False,
            server_default="automatica",
        ),
        sa.Column(
            "principal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_licitacoes_planilhas_licitacao_id",
        "licitacoes_planilhas_orcamentarias",
        ["licitacao_id"],
    )
    op.create_index(
        "ix_licitacoes_planilhas_anexo_id",
        "licitacoes_planilhas_orcamentarias",
        ["anexo_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("licitacoes_planilhas_orcamentarias")
    op.drop_table("licitacoes_pastas_projeto")
```

- [ ] **Step 7: Suíte completa + lint**

Run: `cd apps/api && uv run ruff check . && uv run --extra dev pytest -q`
Expected: tudo verde.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/modules/licitacoes/models.py \
  apps/api/app/modules/licitacoes/schemas.py \
  apps/api/alembic/versions/a7b8c9d0e1f2_captador_pastas_planilhas.py \
  apps/api/tests/test_licitacoes_processamento.py
git commit -m "feat(captador): models PastaProjeto + PlanilhaOrcamentaria com migration"
```

---

### Task 3: `ensure_project_folder` no storage (Local + OneDrive) e `create_folder` no client Graph

**Files:**
- Modify: `apps/api/app/modules/licitacoes/storage.py`
- Modify: `apps/api/app/integrations/onedrive/client.py`
- Modify: `apps/api/app/integrations/onedrive/storage.py`
- Test: `apps/api/tests/test_onedrive_storage.py` (append) e `apps/api/tests/test_licitacoes_processamento.py` (append)

**Interfaces:**
- Consumes: `OneDriveClient._full_path`, `_auth_header`, `GRAPH_BASE`, `OneDriveError` (já existem em `client.py`); `OneDriveMockClient._full_path`, `_id_for`, `_meta`.
- Produces (usado pela Task 5):
  - Protocolo `EditaisStorage` ganha: `async def ensure_project_folder(self, *, licitacao_id: int, nome_pasta: str) -> tuple[str, str | None]` — retorna `(caminho, link_ou_None)`.
  - `OneDriveClient.create_folder(*, relative_path: str) -> dict[str, Any]` — idempotente, retorna item JSON com `id` e `webUrl`.
  - `OneDriveMockClient.create_folder` com o mesmo contrato (webUrl `https://onedrive.mock/folders/{item_id}`).

- [ ] **Step 1: Testes que falham**

Append em `apps/api/tests/test_licitacoes_processamento.py`:

```python
@pytest.mark.asyncio
async def test_local_storage_ensure_project_folder(tmp_path) -> None:
    from app.modules.licitacoes.storage import LocalStorage

    storage = LocalStorage(tmp_path)
    caminho, link = await storage.ensure_project_folder(
        licitacao_id=42, nome_pasta="mg-bh-prefeitura-002_2026"
    )
    assert caminho == str(tmp_path / "42")
    assert (tmp_path / "42").is_dir()
    assert link is None
    # Idempotente: segunda chamada nao explode e retorna o mesmo caminho.
    caminho2, _ = await storage.ensure_project_folder(
        licitacao_id=42, nome_pasta="mg-bh-prefeitura-002_2026"
    )
    assert caminho2 == caminho
```

Append em `apps/api/tests/test_onedrive_storage.py`:

```python
@pytest.mark.asyncio
async def test_onedrive_storage_ensure_project_folder_retorna_weburl() -> None:
    from app.integrations.onedrive.client import OneDriveMockClient
    from app.integrations.onedrive.storage import OneDriveStorage

    client = OneDriveMockClient()
    storage = OneDriveStorage(client)
    caminho, link = await storage.ensure_project_folder(
        licitacao_id=42, nome_pasta="mg-bh-prefeitura-002_2026"
    )
    assert caminho.endswith("/42")
    assert link is not None and link.startswith("https://onedrive.mock/folders/")
    # Idempotente: mesmo id na segunda chamada.
    caminho2, link2 = await storage.ensure_project_folder(
        licitacao_id=42, nome_pasta="qualquer"
    )
    assert (caminho2, link2) == (caminho, link)
```

(Confira os imports existentes no topo de `test_onedrive_storage.py`; `pytest` já está importado lá.)

- [ ] **Step 2: Rodar para confirmar que falham**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py tests/test_onedrive_storage.py -v -k ensure_project_folder`
Expected: FAIL — `AttributeError: ... has no attribute 'ensure_project_folder'`

- [ ] **Step 3: Protocolo + LocalStorage**

Em `apps/api/app/modules/licitacoes/storage.py`, dentro da classe `EditaisStorage(Protocol)`, após `delete`:

```python
    async def ensure_project_folder(
        self, *, licitacao_id: int, nome_pasta: str
    ) -> tuple[str, str | None]:
        """Garante a pasta do projeto e retorna `(caminho, link_ou_None)`.

        `nome_pasta` e o slug humano de exibicao; o backend decide se
        consegue usa-lo fisicamente (o layout atual mantem a pasta
        fisica `{licitacao_id}` pela idempotencia do D.4).
        """
```

Na classe `LocalStorage`, após `delete`:

```python
    async def ensure_project_folder(
        self, *, licitacao_id: int, nome_pasta: str
    ) -> tuple[str, str | None]:
        # `nome_pasta` e so exibicao no backend local -- a pasta fisica
        # segue `{licitacao_id}`, onde o D.4 ja grava os anexos.
        subdir = self._root / str(licitacao_id)
        subdir.mkdir(parents=True, exist_ok=True)
        return str(subdir), None
```

- [ ] **Step 4: `create_folder` no client Graph real e no mock**

Em `apps/api/app/integrations/onedrive/client.py`, na classe `OneDriveClient`, após o método `delete`:

```python
    async def create_folder(self, *, relative_path: str) -> dict[str, Any]:
        """Garante a pasta `{root_folder}/{relative_path}` no drive.

        Idempotente: se ja existe, retorna o item existente. Retorna o
        JSON do item (com `id` e `webUrl`).
        """
        path = self._full_path(relative_path)
        get_url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/root:/{path}"
        try:
            r = await self._client.get(get_url, headers=await self._auth_header())
        except httpx.HTTPError as exc:
            raise OneDriveError(f"get folder Graph falhou: {exc}") from exc
        if r.status_code == 200:
            return r.json()
        if r.status_code != 404:
            raise OneDriveError(
                f"get folder Graph falhou ({r.status_code}): {r.text[:200]}"
            )

        parent, _, name = path.rpartition("/")
        if parent:
            create_url = (
                f"{GRAPH_BASE}/drives/{self._drive_id}/items/root:/{parent}:/children"
            )
        else:
            create_url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/root/children"
        body = {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        }
        try:
            r = await self._client.post(
                create_url, headers=await self._auth_header(), json=body
            )
        except httpx.HTTPError as exc:
            raise OneDriveError(f"create folder Graph falhou: {exc}") from exc
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 409:
            # Corrida com outra criacao: a pasta passou a existir. Rele.
            r = await self._client.get(get_url, headers=await self._auth_header())
            if r.status_code == 200:
                return r.json()
        raise OneDriveError(
            f"create folder Graph falhou ({r.status_code}): {r.text[:200]}"
        )
```

Na classe `OneDriveMockClient`, após o método `delete`:

```python
    async def create_folder(self, *, relative_path: str) -> dict[str, Any]:
        path = self._full_path(relative_path)
        item_id = self._id_for(f"folder:{path}")
        self._meta.setdefault(
            item_id,
            {
                "path": path,
                "name": relative_path.rsplit("/", 1)[-1],
                "size": 0,
                "last_modified": "2026-04-23T00:00:00Z",
                "folder": True,
            },
        )
        return {
            "id": item_id,
            "name": relative_path.rsplit("/", 1)[-1],
            "webUrl": f"https://onedrive.mock/folders/{item_id}",
            "folder": {},
            "source": "onedrive_mock",
        }
```

- [ ] **Step 5: `ensure_project_folder` no `OneDriveStorage`**

Em `apps/api/app/integrations/onedrive/storage.py`, na classe `OneDriveStorage`, após `delete`:

```python
    async def ensure_project_folder(
        self, *, licitacao_id: int, nome_pasta: str
    ) -> tuple[str, str | None]:
        """Pasta fisica `{licitacao_id}` (mesma dos anexos do D.4).

        `nome_pasta` (slug humano) fica so como exibicao em
        `PastaProjeto.nome_pasta` -- decisao registrada no plano da
        Squad 2; revisitar quando o SharePoint corporativo chegar.
        """
        try:
            item = await self._client.create_folder(
                relative_path=str(licitacao_id)
            )
        except OneDriveError as exc:
            raise OSError(f"OneDrive create_folder falhou: {exc}") from exc
        caminho = f"{self._client.root_folder}/{licitacao_id}"
        return caminho, item.get("webUrl")
```

- [ ] **Step 6: Rodar até passar + suíte + lint**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py tests/test_onedrive_storage.py -v && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/storage.py \
  apps/api/app/integrations/onedrive/client.py \
  apps/api/app/integrations/onedrive/storage.py \
  apps/api/tests/test_onedrive_storage.py \
  apps/api/tests/test_licitacoes_processamento.py
git commit -m "feat(captador): ensure_project_folder no storage + create_folder idempotente no Graph"
```

---

### Task 4: Fábrica de storage compartilhada API/worker (`storage_factory.py`)

**Files:**
- Create: `apps/api/app/modules/licitacoes/storage_factory.py`
- Modify: `apps/api/app/modules/licitacoes/router.py:71-101` (`get_editais_storage` delega para a fábrica)
- Test: `apps/api/tests/test_licitacoes_processamento.py` (append)

**Interfaces:**
- Consumes: `Settings` (`storage_backend`, `editais_storage_path`, `ms_graph_*`), `build_onedrive_client`, `OneDriveStorage`, `LocalStorage`.
- Produces (usado pela Task 6 no worker):
  - `@asynccontextmanager editais_storage(settings: Settings) -> AsyncIterator[EditaisStorage]`

- [ ] **Step 1: Teste que falha**

Append em `apps/api/tests/test_licitacoes_processamento.py`:

```python
@pytest.mark.asyncio
async def test_storage_factory_local(tmp_path, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.modules.licitacoes.storage import LocalStorage
    from app.modules.licitacoes.storage_factory import editais_storage

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EDITAIS_STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    try:
        async with editais_storage(get_settings()) as storage:
            assert isinstance(storage, LocalStorage)
            assert storage.root == tmp_path
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_storage_factory_onedrive_sem_credenciais_usa_mock(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.integrations.onedrive.storage import OneDriveStorage
    from app.modules.licitacoes.storage_factory import editais_storage

    monkeypatch.setenv("STORAGE_BACKEND", "onedrive")
    monkeypatch.delenv("MS_GRAPH_TENANT_ID", raising=False)
    get_settings.cache_clear()
    try:
        async with editais_storage(get_settings()) as storage:
            assert isinstance(storage, OneDriveStorage)
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 2: Rodar para confirmar que falha**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py -v -k storage_factory`
Expected: FAIL — `ModuleNotFoundError: ... storage_factory`

- [ ] **Step 3: Implementar a fábrica**

Criar `apps/api/app/modules/licitacoes/storage_factory.py`:

```python
"""Fabrica do backend `EditaisStorage` compartilhada entre API e worker.

Antes da Squad 2, a selecao de backend vivia dentro da dependency
FastAPI `get_editais_storage` (router.py) -- o worker Celery nao
conseguia reutiliza-la. Extraida para ca como async context manager:
o `finally` fecha o `httpx.AsyncClient` interno do OneDriveClient
(sem isso, cada uso vaza um pool TCP).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import Settings
from app.integrations.onedrive.client import build_onedrive_client
from app.integrations.onedrive.storage import OneDriveStorage
from app.modules.licitacoes.storage import EditaisStorage, LocalStorage


@asynccontextmanager
async def editais_storage(settings: Settings) -> AsyncIterator[EditaisStorage]:
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.ms_graph_root_folder,
        )
        try:
            yield OneDriveStorage(client)
        finally:
            await client.aclose()
        return
    yield LocalStorage(settings.editais_storage_path)
```

- [ ] **Step 4: Router delega para a fábrica**

Em `apps/api/app/modules/licitacoes/router.py`, substituir o corpo de `get_editais_storage` (linhas ~71–101) por:

```python
async def get_editais_storage() -> AsyncIterator[EditaisStorage]:
    """Storage backend selecionado por config -- ver `storage_factory`.

    Async generator para o FastAPI fechar o client httpx interno ao
    final da request (mesmo padrao de `pncp` e `llm` neste arquivo).
    """
    async with editais_storage(get_settings()) as storage:
        yield storage
```

E adicionar o import no bloco de imports do módulo:

```python
from app.modules.licitacoes.storage_factory import editais_storage
```

(Os imports de `build_onedrive_client` e `OneDriveStorage` no router ficam sem uso — removê-los.)

- [ ] **Step 5: Rodar tudo + lint**

Run: `cd apps/api && uv run ruff check . && uv run --extra dev pytest -q`
Expected: verde — em particular `tests/test_licitacoes_editais.py` e `tests/test_licitacoes_router.py` continuam passando (o refactor não muda comportamento).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/modules/licitacoes/storage_factory.py \
  apps/api/app/modules/licitacoes/router.py \
  apps/api/tests/test_licitacoes_processamento.py
git commit -m "refactor(captador): fabrica editais_storage compartilhada entre API e worker"
```

---

### Task 5: Serviço `processar_aprovado` (máquina de status + orquestração)

> **Pré-flight:** rode o grep da seção "Interfaces com a Squad 1". Sem `status_triagem` e `DecisaoTriagem` no models.py, PARE aqui.

**Files:**
- Create: `apps/api/app/modules/licitacoes/processamento.py`
- Modify: `apps/api/app/modules/licitacoes/schemas.py` (append `ProcessamentoResult`)
- Test: `apps/api/tests/test_licitacoes_processamento.py` (append)

**Interfaces:**
- Consumes: `download_edital_for_licitacao(db, *, licitacao_id, pncp, storage) -> EditalDownloadResult` (D.4, retorna `.status` em `{"completed","empty","failed"}`); `classificar_anexo`, `LIMIAR_PRINCIPAL`, `normalizar` (Task 1); `PastaProjeto`, `PlanilhaOrcamentaria` (Task 2); `storage.ensure_project_folder` (Task 3); `storage.read(storage_path) -> bytes`; `Licitacao.status_triagem` (Squad 1).
- Produces (usado pelas Tasks 6–7):
  - Constantes `STATUS_NOVO_CAPTADO = "novo_captado"`, `STATUS_EM_ANALISE = "em_analise"`, `STATUS_APROVADO = "aprovado"`, `STATUS_REJEITADO = "rejeitado"`, `STATUS_PROCESSANDO_ANEXOS = "processando_anexos"`, `STATUS_COMPLETO = "completo"`, `STATUS_SEM_PLANILHA = "sem_planilha"`, `STATUS_ERRO_PORTAL = "erro_portal"`, `STATUS_ERRO_SHAREPOINT = "erro_sharepoint"`
  - `class ProcessamentoNaoPermitido(RuntimeError)`
  - `async def processar_aprovado(db: AsyncSession, *, licitacao_id: int, pncp: PncpClient, storage: EditaisStorage) -> ProcessamentoResult`
  - `async def marcar_planilha_principal(db: AsyncSession, *, licitacao_id: int, planilha_id: int) -> PlanilhaOrcamentaria`
  - `def montar_nome_pasta(licitacao: Licitacao) -> str`
  - Schema `ProcessamentoResult(BaseModel)`: `licitacao_id: int`, `status_triagem: str`, `anexos_count: int`, `planilha_encontrada: bool`, `planilha_anexo_id: int | None`, `pasta_link: str | None`, `error_message: str | None`

- [ ] **Step 1: Schema `ProcessamentoResult`**

Append em `apps/api/app/modules/licitacoes/schemas.py`:

```python
class ProcessamentoResult(BaseModel):
    """Resultado do processamento pos-aprovacao (Captador Squad 2)."""

    licitacao_id: int
    status_triagem: str
    anexos_count: int = 0
    planilha_encontrada: bool = False
    planilha_anexo_id: int | None = None
    pasta_link: str | None = None
    error_message: str | None = None
```

- [ ] **Step 2: Testes que falham**

Append em `apps/api/tests/test_licitacoes_processamento.py` (novo bloco no fim; note o fake do PNCP):

```python
# --- processar_aprovado ----------------------------------------------------

XLSX_PLANILHA = None  # preenchido no primeiro uso para nao pagar o custo em import


def _xlsx_planilha_bytes() -> bytes:
    global XLSX_PLANILHA
    if XLSX_PLANILHA is None:
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Item", "Unid", "Quant", "Preço Unitário", "Total"])
        buf = io.BytesIO()
        wb.save(buf)
        XLSX_PLANILHA = buf.getvalue()
    return XLSX_PLANILHA


class FakePncp:
    """Duble do PncpClient: 2 anexos, um PDF e uma planilha XLSX."""

    def __init__(self, arquivos=None, fail=False) -> None:
        self._fail = fail
        from app.integrations.pncp.client import PncpArquivo

        self.arquivos = arquivos if arquivos is not None else [
            PncpArquivo(
                sequencial_documento=1,
                titulo="EDITAL_PREGAO_002",
                tipo_documento_descricao="Edital",
                url="https://pncp.gov.br/arquivos/1",
                status_ativo=True,
                data_publicacao_pncp=None,
            ),
            PncpArquivo(
                sequencial_documento=2,
                titulo="Planilha_Orcamentaria",
                tipo_documento_descricao="Outros",
                url="https://pncp.gov.br/arquivos/2",
                status_ativo=True,
                data_publicacao_pncp=None,
            ),
        ]

    async def list_arquivos(self, *, cnpj, ano, sequencial):
        import httpx

        if self._fail:
            raise httpx.ConnectError("pncp fora do ar")
        return self.arquivos

    async def stream_arquivo(self, url):
        async def chunks():
            if url.endswith("/2"):
                yield _xlsx_planilha_bytes()
            else:
                yield b"%PDF-1.7 conteudo"

        filename = "uivd1biu.xlsx" if url.endswith("/2") else "uivd1biu.pdf"
        ct = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if url.endswith("/2")
            else "application/pdf"
        )
        return chunks(), filename, ct

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_processar_aprovado_fluxo_completo(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_COMPLETO,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(),
        storage=LocalStorage(tmp_path),
    )

    assert result.status_triagem == STATUS_COMPLETO
    assert result.anexos_count == 2
    assert result.planilha_encontrada is True

    await db_session.refresh(lic)
    assert lic.status_triagem == STATUS_COMPLETO

    pasta = (await db_session.execute(select(PastaProjeto))).scalar_one()
    assert pasta.licitacao_id == lic.id
    assert pasta.nome_pasta.startswith("mg-belo_horizonte")

    planilha = (
        await db_session.execute(
            select(PlanilhaOrcamentaria).where(PlanilhaOrcamentaria.principal)
        )
    ).scalar_one()
    assert planilha.nome_arquivo.endswith(".xlsx")
    assert planilha.link == "https://pncp.gov.br/arquivos/2"
    assert planilha.score_classificacao >= 10


@pytest.mark.asyncio
async def test_processar_aprovado_sem_planilha(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.integrations.pncp.client import PncpArquivo
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_SEM_PLANILHA,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-8", sequencial_compra=8)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    so_pdf = [
        PncpArquivo(
            sequencial_documento=1,
            titulo="EDITAL",
            tipo_documento_descricao="Edital",
            url="https://pncp.gov.br/arquivos/1",
            status_ativo=True,
            data_publicacao_pncp=None,
        )
    ]
    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(arquivos=so_pdf),
        storage=LocalStorage(tmp_path),
    )
    assert result.status_triagem == STATUS_SEM_PLANILHA
    assert result.planilha_encontrada is False


@pytest.mark.asyncio
async def test_processar_aprovado_pncp_fora_vira_erro_portal(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_ERRO_PORTAL,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-9", sequencial_compra=9)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(fail=True),
        storage=LocalStorage(tmp_path),
    )
    assert result.status_triagem == STATUS_ERRO_PORTAL
    assert result.error_message


@pytest.mark.asyncio
async def test_processar_rejeitado_e_bloqueado(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_REJEITADO,
        ProcessamentoNaoPermitido,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-10", sequencial_compra=10)
    lic.status_triagem = STATUS_REJEITADO
    await db_session.commit()

    with pytest.raises(ProcessamentoNaoPermitido):
        await processar_aprovado(
            db_session,
            licitacao_id=lic.id,
            pncp=FakePncp(),
            storage=LocalStorage(tmp_path),
        )


@pytest.mark.asyncio
async def test_reprocessar_completo_e_idempotente(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_COMPLETO,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-11", sequencial_compra=11)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    storage = LocalStorage(tmp_path)
    await processar_aprovado(
        db_session, licitacao_id=lic.id, pncp=FakePncp(), storage=storage
    )
    result2 = await processar_aprovado(
        db_session, licitacao_id=lic.id, pncp=FakePncp(), storage=storage
    )
    assert result2.status_triagem == STATUS_COMPLETO

    pastas = (await db_session.execute(select(PastaProjeto))).scalars().all()
    planilhas = (
        await db_session.execute(select(PlanilhaOrcamentaria))
    ).scalars().all()
    assert len([p for p in pastas if p.licitacao_id == lic.id]) == 1
    assert len([p for p in planilhas if p.licitacao_id == lic.id]) == 1


@pytest.mark.asyncio
async def test_marcar_planilha_principal_e_sticky(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.integrations.pncp.client import PncpArquivo
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        marcar_planilha_principal,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-12", sequencial_compra=12)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    duas_planilhas = [
        PncpArquivo(
            sequencial_documento=1,
            titulo="Planilha_Orcamentaria",
            tipo_documento_descricao="Outros",
            url="https://pncp.gov.br/arquivos/2",
            status_ativo=True,
            data_publicacao_pncp=None,
        ),
        PncpArquivo(
            sequencial_documento=2,
            titulo="Cronograma",
            tipo_documento_descricao="Outros",
            url="https://pncp.gov.br/arquivos/2",
            status_ativo=True,
            data_publicacao_pncp=None,
        ),
    ]
    storage = LocalStorage(tmp_path)
    await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(arquivos=duas_planilhas),
        storage=storage,
    )
    rows = (
        (await db_session.execute(select(PlanilhaOrcamentaria))).scalars().all()
    )
    rows = [r for r in rows if r.licitacao_id == lic.id]
    assert len(rows) == 2
    secundaria = next(r for r in rows if not r.principal)

    escolhida = await marcar_planilha_principal(
        db_session, licitacao_id=lic.id, planilha_id=secundaria.id
    )
    assert escolhida.principal is True
    assert escolhida.status_validacao == "principal_manual"

    # Reprocessar NAO desfaz a escolha manual.
    await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(arquivos=duas_planilhas),
        storage=storage,
    )
    await db_session.refresh(escolhida)
    assert escolhida.principal is True
```

Nota: confira a assinatura real de `PncpArquivo` em `app/integrations/pncp/client.py` antes de rodar — se os campos forem posicionais ou tiverem outros nomes (`tipo_documento_descricao` etc.), ajuste o teste para casar com o dataclass real. O `FakePncp.stream_arquivo` retorna `(AsyncIterator[bytes], filename, content_type)` — mesmo contrato usado por `editais._download_single`.

- [ ] **Step 3: Rodar para confirmar que falham**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py -v -k "processar or marcar"`
Expected: FAIL — `ModuleNotFoundError: ... processamento`

- [ ] **Step 4: Implementar `processamento.py`**

Criar `apps/api/app/modules/licitacoes/processamento.py`:

```python
"""Processamento pos-aprovacao do Captador (Squad 2).

Maquina de status da triagem (Projeto Tecnico, secao 5.1):

    novo_captado -> em_analise -> aprovado    (Squad 1, Tela de Captacao)
    aprovado -> processando_anexos -> completo | sem_planilha
                                   -> erro_portal | erro_sharepoint

`processar_aprovado` e idempotente: pode re-rodar sobre completo /
sem_planilha / erro_* (retry manual) sem duplicar pasta nem planilhas.
Estados que NUNCA processam: novo_captado, em_analise, rejeitado.
"""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.editais import get_edital, list_anexos
from app.modules.licitacoes.editais import (
    download_edital_for_licitacao,
)
from app.modules.licitacoes.identificacao_planilha import (
    LIMIAR_PRINCIPAL,
    classificar_anexo,
    normalizar,
)
from app.modules.licitacoes.models import (
    AnexoEdital,
    Licitacao,
    PastaProjeto,
    PlanilhaOrcamentaria,
)
from app.modules.licitacoes.schemas import ProcessamentoResult
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)

STATUS_NOVO_CAPTADO = "novo_captado"
STATUS_EM_ANALISE = "em_analise"
STATUS_APROVADO = "aprovado"
STATUS_REJEITADO = "rejeitado"
STATUS_PROCESSANDO_ANEXOS = "processando_anexos"
STATUS_COMPLETO = "completo"
STATUS_SEM_PLANILHA = "sem_planilha"
STATUS_ERRO_PORTAL = "erro_portal"
STATUS_ERRO_SHAREPOINT = "erro_sharepoint"

_PROCESSAVEIS = frozenset(
    {
        STATUS_APROVADO,
        STATUS_PROCESSANDO_ANEXOS,
        STATUS_COMPLETO,
        STATUS_SEM_PLANILHA,
        STATUS_ERRO_PORTAL,
        STATUS_ERRO_SHAREPOINT,
    }
)

_PASTA_SAFE = re.compile(r"[^a-z0-9._-]+")


class ProcessamentoNaoPermitido(RuntimeError):
    """Licitacao nao esta num status que permita processar anexos."""


def montar_nome_pasta(licitacao: Licitacao) -> str:
    """Slug humano `<uf>-<municipio>-<orgao>-<numero>` para exibicao."""
    partes = [
        licitacao.uf_sigla or "uf",
        licitacao.municipio_nome or "municipio",
        licitacao.orgao_razao_social or "orgao",
        licitacao.numero_compra or str(licitacao.id),
    ]
    segmentos = [
        _PASTA_SAFE.sub("_", normalizar(p)).strip("_") or "x" for p in partes
    ]
    return "-".join(segmentos)[:120]


async def processar_aprovado(
    db: AsyncSession,
    *,
    licitacao_id: int,
    pncp: PncpClient,
    storage: EditaisStorage,
) -> ProcessamentoResult:
    licitacao = await db.get(Licitacao, licitacao_id)
    if licitacao is None:
        raise ValueError(f"Licitacao {licitacao_id} not found")
    if licitacao.status_triagem not in _PROCESSAVEIS:
        raise ProcessamentoNaoPermitido(
            f"status_triagem={licitacao.status_triagem!r} nao permite processar"
        )

    licitacao.status_triagem = STATUS_PROCESSANDO_ANEXOS
    await db.commit()

    # 1. Anexos (reusa D.4, idempotente).
    download = await download_edital_for_licitacao(
        db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
    )
    if download.status == "failed":
        licitacao.status_triagem = STATUS_ERRO_PORTAL
        await db.commit()
        return ProcessamentoResult(
            licitacao_id=licitacao_id,
            status_triagem=STATUS_ERRO_PORTAL,
            anexos_count=download.anexos_count,
            error_message=download.error_message or "download falhou",
        )

    # 2. Pasta do projeto.
    try:
        caminho, link = await storage.ensure_project_folder(
            licitacao_id=licitacao_id, nome_pasta=montar_nome_pasta(licitacao)
        )
    except OSError as exc:
        logger.warning("pasta do projeto falhou (lic %s): %s", licitacao_id, exc)
        licitacao.status_triagem = STATUS_ERRO_SHAREPOINT
        await db.commit()
        return ProcessamentoResult(
            licitacao_id=licitacao_id,
            status_triagem=STATUS_ERRO_SHAREPOINT,
            anexos_count=download.anexos_count,
            error_message=str(exc),
        )
    await _upsert_pasta(
        db, licitacao=licitacao, caminho=caminho, link=link, storage=storage
    )

    # 3. Classificacao dos anexos + planilha principal.
    principal_anexo_id = await _classificar_planilhas(
        db, licitacao_id=licitacao_id, storage=storage
    )

    licitacao.status_triagem = (
        STATUS_COMPLETO if principal_anexo_id is not None else STATUS_SEM_PLANILHA
    )
    await db.commit()
    pasta_link = link
    return ProcessamentoResult(
        licitacao_id=licitacao_id,
        status_triagem=licitacao.status_triagem,
        anexos_count=download.anexos_count,
        planilha_encontrada=principal_anexo_id is not None,
        planilha_anexo_id=principal_anexo_id,
        pasta_link=pasta_link,
    )


async def _upsert_pasta(
    db: AsyncSession,
    *,
    licitacao: Licitacao,
    caminho: str,
    link: str | None,
    storage: EditaisStorage,
) -> None:
    stmt = select(PastaProjeto).where(PastaProjeto.licitacao_id == licitacao.id)
    pasta = (await db.execute(stmt)).scalar_one_or_none()
    backend = type(storage).__name__.replace("Storage", "").lower() or "local"
    if pasta is None:
        pasta = PastaProjeto(
            licitacao_id=licitacao.id,
            nome_pasta=montar_nome_pasta(licitacao),
            caminho=caminho,
            link_pasta=link,
            storage_backend=backend,
        )
        db.add(pasta)
    else:
        pasta.caminho = caminho
        pasta.link_pasta = link
        pasta.storage_backend = backend
        pasta.status = "criada"
    await db.flush()


async def _classificar_planilhas(
    db: AsyncSession, *, licitacao_id: int, storage: EditaisStorage
) -> int | None:
    """Upsert de `PlanilhaOrcamentaria` por anexo elegivel.

    Retorna o `anexo_id` da planilha principal, ou None. Escolha manual
    previa (`status_validacao=principal_manual`) e respeitada.
    """
    edital = await get_edital(db, licitacao_id)
    if edital is None:
        return None
    anexos = await list_anexos(db, edital.id)

    stmt = select(PlanilhaOrcamentaria).where(
        PlanilhaOrcamentaria.licitacao_id == licitacao_id
    )
    existentes = {
        p.anexo_id: p for p in (await db.execute(stmt)).scalars().all()
    }

    candidatas: list[PlanilhaOrcamentaria] = []
    for anexo in anexos:
        extensao = PurePosixPath(anexo.filename).suffix.lower()
        conteudo: bytes | None = None
        if extensao in {".xlsx", ".ods"}:
            try:
                conteudo = await storage.read(anexo.storage_path)
            except (OSError, FileNotFoundError) as exc:
                logger.warning(
                    "leitura do anexo %s falhou, score so por nome: %s",
                    anexo.id,
                    exc,
                )
        score = classificar_anexo(anexo.filename, conteudo)
        if score is None:
            continue
        row = existentes.get(anexo.id)
        if row is None:
            row = PlanilhaOrcamentaria(
                licitacao_id=licitacao_id,
                anexo_id=anexo.id,
                nome_arquivo=anexo.filename,
                extensao=extensao,
                score_classificacao=score,
                link=anexo.source_url,
            )
            db.add(row)
        else:
            row.score_classificacao = score
            row.link = anexo.source_url
        candidatas.append(row)
    await db.flush()

    if not candidatas:
        return None

    manual = next(
        (c for c in candidatas if c.status_validacao == "principal_manual"), None
    )
    if manual is not None:
        principal = manual
    else:
        melhor = max(candidatas, key=lambda c: c.score_classificacao)
        principal = melhor if melhor.score_classificacao >= LIMIAR_PRINCIPAL else None

    for c in candidatas:
        c.principal = principal is not None and c.id == principal.id
    await db.flush()
    return principal.anexo_id if principal is not None else None


async def marcar_planilha_principal(
    db: AsyncSession, *, licitacao_id: int, planilha_id: int
) -> PlanilhaOrcamentaria:
    """Analista elege manualmente outra planilha como principal."""
    stmt = select(PlanilhaOrcamentaria).where(
        PlanilhaOrcamentaria.licitacao_id == licitacao_id
    )
    rows = list((await db.execute(stmt)).scalars().all())
    alvo = next((r for r in rows if r.id == planilha_id), None)
    if alvo is None:
        raise ValueError(
            f"Planilha {planilha_id} nao encontrada para licitacao {licitacao_id}"
        )
    for r in rows:
        r.principal = r.id == planilha_id
        if r.status_validacao == "principal_manual" and r.id != planilha_id:
            r.status_validacao = "automatica"
    alvo.status_validacao = "principal_manual"

    licitacao = await db.get(Licitacao, licitacao_id)
    if licitacao is not None and licitacao.status_triagem == STATUS_SEM_PLANILHA:
        licitacao.status_triagem = STATUS_COMPLETO
    await db.commit()
    await db.refresh(alvo)
    return alvo
```

Atenção a um detalhe do `_classificar_planilhas`: `c.id == principal.id` exige `flush` antes (feito) para os ids existirem.

- [ ] **Step 5: Rodar até passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_processamento.py -v`
Expected: PASS (todos). Ajuste esperado mais comum: campos do `PncpArquivo` (ver nota do Step 2).

- [ ] **Step 6: Suíte completa + lint**

Run: `cd apps/api && uv run ruff check . && uv run --extra dev pytest -q`
Expected: verde. (Ruff vai reclamar do import duplicado de `editais` — consolide em um único `from app.modules.licitacoes.editais import download_edital_for_licitacao, get_edital, list_anexos`.)

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/processamento.py \
  apps/api/app/modules/licitacoes/schemas.py \
  apps/api/tests/test_licitacoes_processamento.py
git commit -m "feat(captador): servico processar_aprovado com maquina de status e planilha principal"
```

---

### Task 6: Endpoints de processamento + task Celery

**Files:**
- Modify: `apps/api/app/modules/licitacoes/router.py`
- Modify: `apps/workers/worker/tasks/licitacoes.py` (append)
- Test: `apps/api/tests/test_licitacoes_triagem.py` (novo)

**Interfaces:**
- Consumes: `processar_aprovado`, `marcar_planilha_principal`, `ProcessamentoNaoPermitido` (Task 5); deps `get_pncp_client`, `get_editais_storage`, `get_current_user` (router existente); `editais_storage` (Task 4).
- Produces:
  - `POST /api/v1/licitacoes/{licitacao_id}/processar-anexos` → `ProcessamentoResult` (auth; 404 licitação inexistente; 409 status não processável)
  - `PATCH /api/v1/licitacoes/{licitacao_id}/planilhas/{planilha_id}` body `{"principal": true}` → `PlanilhaOrcamentariaRead` (auth)
  - Task Celery `worker.tasks.licitacoes.processar_edital_aprovado(licitacao_id: int)` (nome consumido pela Squad 1 via `send_task`)
  - Schema `PlanilhaPrincipalUpdate(BaseModel)`: `principal: bool = True`

- [ ] **Step 1: Testes que falham**

Criar `apps/api/tests/test_licitacoes_triagem.py`:

```python
"""Tests dos endpoints do Captador Squad 2 (processar-anexos, planilhas, triagem)."""
from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import Licitacao
from app.modules.licitacoes.processamento import (
    STATUS_APROVADO,
    STATUS_NOVO_CAPTADO,
)
from app.modules.licitacoes.router import (
    get_editais_storage,
    get_pncp_client,
)
from app.main import app

from tests.test_licitacoes_processamento import FakePncp, _mk_licitacao


def _override_deps(tmp_path, pncp=None):
    from app.modules.licitacoes.storage import LocalStorage

    async def _storage():
        yield LocalStorage(tmp_path)

    def _pncp():
        return pncp or FakePncp()

    app.dependency_overrides[get_editais_storage] = _storage
    app.dependency_overrides[get_pncp_client] = _pncp


def _clear_deps():
    app.dependency_overrides.pop(get_editais_storage, None)
    app.dependency_overrides.pop(get_pncp_client, None)


@pytest.mark.asyncio
async def test_processar_anexos_endpoint_exige_auth(
    api_client: AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    lic = await _mk_licitacao(db_session)
    resp = await api_client.post(f"/api/v1/licitacoes/{lic.id}/processar-anexos")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_processar_anexos_endpoint_completo(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    lic = await _mk_licitacao(db_session)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
    finally:
        _clear_deps()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status_triagem"] == "completo"
    assert body["planilha_encontrada"] is True


@pytest.mark.asyncio
async def test_processar_anexos_status_invalido_da_409(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    lic = await _mk_licitacao(db_session, external_id="y-1", sequencial_compra=21)
    assert lic.status_triagem == STATUS_NOVO_CAPTADO
    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
    finally:
        _clear_deps()
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_processar_anexos_licitacao_inexistente_da_404(
    api_client: AsyncClient, tmp_path, auth_headers: dict[str, str]
) -> None:
    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/999999/processar-anexos", headers=auth_headers
        )
    finally:
        _clear_deps()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_planilha_principal(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    from sqlalchemy import select

    from app.modules.licitacoes.models import PlanilhaOrcamentaria

    lic = await _mk_licitacao(db_session, external_id="y-2", sequencial_compra=22)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
        assert resp.status_code == 200
        planilha = (
            await db_session.execute(
                select(PlanilhaOrcamentaria).where(
                    PlanilhaOrcamentaria.licitacao_id == lic.id
                )
            )
        ).scalars().first()
        resp2 = await api_client.patch(
            f"/api/v1/licitacoes/{lic.id}/planilhas/{planilha.id}",
            json={"principal": True},
            headers=auth_headers,
        )
    finally:
        _clear_deps()
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status_validacao"] == "principal_manual"


def test_worker_task_processar_edital_aprovado_registrada() -> None:
    import sys
    from pathlib import Path

    workers_dir = Path(__file__).resolve().parents[2] / "workers"
    sys.path.insert(0, str(workers_dir))
    try:
        from worker.main import celery_app  # noqa: F401
        import worker.tasks.licitacoes  # noqa: F401

        assert (
            "worker.tasks.licitacoes.processar_edital_aprovado"
            in celery_app.tasks
        )
    finally:
        sys.path.remove(str(workers_dir))
```

Nota: se `apps/workers` não estiver importável a partir dos testes da API no seu ambiente (depende do PYTHONPATH do CI), mova `test_worker_task_processar_edital_aprovado_registrada` para `apps/workers/tests/test_licitacoes_tasks.py` seguindo o padrão de `apps/workers/tests/test_correlation_propagation.py`.

- [ ] **Step 2: Rodar para confirmar que falham**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v`
Expected: FAIL — 404 nos endpoints novos (rota não existe) e ImportError/KeyError na task.

- [ ] **Step 3: Endpoints no router**

Em `apps/api/app/modules/licitacoes/router.py`:

1. Adicionar aos imports:

```python
from app.modules.licitacoes.processamento import (
    ProcessamentoNaoPermitido,
    marcar_planilha_principal,
    processar_aprovado,
)
from app.modules.licitacoes.schemas import (
    PlanilhaOrcamentariaRead,
    PlanilhaPrincipalUpdate,
    ProcessamentoResult,
)
```

(mescle com os blocos `from app.modules.licitacoes.schemas import (...)` existentes.)

2. Após o endpoint `get_edital_analise_endpoint`/`run_edital_analise_endpoint` (final do arquivo), adicionar:

```python
# --- Captador Squad 2: processamento pos-aprovacao ---


@router.post(
    "/{licitacao_id}/processar-anexos", response_model=ProcessamentoResult
)
async def processar_anexos_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
    pncp: PncpClient = Depends(get_pncp_client),
    storage: EditaisStorage = Depends(get_editais_storage),
    _: User = Depends(get_current_user),
) -> ProcessamentoResult:
    """Disparo manual do processamento (a demo Vercel nao tem worker).

    O caminho normal e a task Celery
    `worker.tasks.licitacoes.processar_edital_aprovado`, despachada
    pela Tela de Captacao ao aprovar.
    """
    try:
        return await processar_aprovado(
            db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProcessamentoNaoPermitido as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await pncp.aclose()


@router.patch(
    "/{licitacao_id}/planilhas/{planilha_id}",
    response_model=PlanilhaOrcamentariaRead,
)
async def marcar_planilha_principal_endpoint(
    licitacao_id: int,
    planilha_id: int,
    payload: PlanilhaPrincipalUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PlanilhaOrcamentariaRead:
    if not payload.principal:
        raise HTTPException(
            status_code=400, detail="apenas principal=true e suportado"
        )
    try:
        row = await marcar_planilha_principal(
            db, licitacao_id=licitacao_id, planilha_id=planilha_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlanilhaOrcamentariaRead.model_validate(row)
```

3. Append em `apps/api/app/modules/licitacoes/schemas.py`:

```python
class PlanilhaPrincipalUpdate(BaseModel):
    principal: bool = True
```

- [ ] **Step 4: Task Celery**

Append em `apps/workers/worker/tasks/licitacoes.py`:

```python
@celery_app.task(name="worker.tasks.licitacoes.processar_edital_aprovado")
def processar_edital_aprovado(licitacao_id: int) -> dict[str, object]:
    """Processa um edital aprovado na triagem (Captador Squad 2).

    Despachada pela Tela de Captacao (Squad 1) via `send_task` quando a
    analista aprova. Baixa anexos, cria pasta do projeto e identifica a
    planilha orcamentaria. Idempotente -- pode ser re-executada.
    """
    return asyncio.run(_run_processamento(licitacao_id))


async def _run_processamento(licitacao_id: int) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.processamento import processar_aprovado
        from app.modules.licitacoes.storage_factory import editais_storage
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    async with SessionLocal() as db:
        pncp = PncpClient(
            base_url=os.getenv(
                "PNCP_BASE_URL", "https://pncp.gov.br/api/consulta"
            )
        )
        try:
            async with editais_storage(settings) as storage:
                result = await processar_aprovado(
                    db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
                )
        finally:
            await pncp.aclose()
    return result.model_dump()
```

- [ ] **Step 5: Rodar até passar + suíte + lint**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py tests/test_licitacoes_processamento.py -v && uv run ruff check .`
Expected: PASS. Depois a suíte inteira: `uv run --extra dev pytest -q`.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/modules/licitacoes/router.py \
  apps/api/app/modules/licitacoes/schemas.py \
  apps/workers/worker/tasks/licitacoes.py \
  apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): endpoint processar-anexos + PATCH planilha principal + task Celery"
```

---

### Task 7: Endpoint consolidado `GET /licitacoes/triagem` (Aba de Triagem)

**Files:**
- Modify: `apps/api/app/modules/licitacoes/router.py` (ATENÇÃO: declarar ANTES de `get_endpoint` `/{licitacao_id}`, ~linha 176)
- Modify: `apps/api/app/modules/licitacoes/schemas.py` (append)
- Create: `apps/api/app/modules/licitacoes/triagem.py`
- Test: `apps/api/tests/test_licitacoes_triagem.py` (append)

**Interfaces:**
- Consumes: `Licitacao.status_triagem`, `DecisaoTriagem` (Squad 1 — ver contrato no topo), `Edital.anexos_count`, `PastaProjeto`, `PlanilhaOrcamentaria` (Task 2).
- Produces:
  - `GET /api/v1/licitacoes/triagem?status=&uf=&page=&page_size=` → `TriagemListResponse` (read, sem auth — padrão do módulo)
  - `async def montar_triagem(db, *, status, uf, page, page_size) -> tuple[list[TriagemRow], int]` em `triagem.py`
  - Schemas: `TriagemRow`, `TriagemListResponse`

- [ ] **Step 1: Testes que falham**

Append em `apps/api/tests/test_licitacoes_triagem.py`:

```python
# --- GET /licitacoes/triagem ----------------------------------------------


@pytest.mark.asyncio
async def test_triagem_lista_com_links(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    lic = await _mk_licitacao(db_session, external_id="y-3", sequencial_compra=23)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
        assert resp.status_code == 200
        resp = await api_client.get("/api/v1/licitacoes/triagem")
    finally:
        _clear_deps()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    row = next(r for r in body["data"] if r["licitacao_id"] == lic.id)
    assert row["status_triagem"] == "completo"
    assert row["link_portal"] == (
        "https://pncp.gov.br/app/editais/12345678000100/2026/23"
    )
    assert row["link_planilha"] == "https://pncp.gov.br/arquivos/2"
    assert row["planilha_nome"] is not None
    assert row["anexos_count"] == 2


@pytest.mark.asyncio
async def test_triagem_filtra_por_status(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    lic = await _mk_licitacao(db_session, external_id="y-4", sequencial_compra=24)
    assert lic.status_triagem == STATUS_NOVO_CAPTADO

    resp = await api_client.get(
        "/api/v1/licitacoes/triagem", params={"status": "novo_captado"}
    )
    assert resp.status_code == 200
    assert all(
        r["status_triagem"] == "novo_captado" for r in resp.json()["data"]
    )


@pytest.mark.asyncio
async def test_triagem_inclui_observacao_da_ultima_decisao(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.modules.licitacoes.models import DecisaoTriagem

    lic = await _mk_licitacao(db_session, external_id="y-5", sequencial_compra=25)
    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id,
            decisao="em_analise",
            observacao="verificar atestado de capacidade",
            usuario_email="analista@primor.com",
        )
    )
    await db_session.commit()

    resp = await api_client.get("/api/v1/licitacoes/triagem")
    assert resp.status_code == 200
    row = next(
        r for r in resp.json()["data"] if r["licitacao_id"] == lic.id
    )
    assert row["observacao"] == "verificar atestado de capacidade"
```

- [ ] **Step 2: Rodar para confirmar que falham**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v -k triagem_`
Expected: FAIL — 422 (rota `/triagem` cai em `/{licitacao_id}` com int inválido) ou 404.

- [ ] **Step 3: Schemas**

Append em `apps/api/app/modules/licitacoes/schemas.py`:

```python
class TriagemRow(BaseModel):
    """Uma linha da Aba de Triagem (Projeto Tecnico, secao 7.2)."""

    licitacao_id: int
    status_triagem: str
    uf_sigla: str | None
    municipio_nome: str | None
    orgao_razao_social: str | None
    objeto_compra: str | None
    modalidade_nome: str | None
    valor_total_estimado: Decimal | None
    data_publicacao_pncp: datetime | None
    link_portal: str | None
    link_pasta: str | None
    link_planilha: str | None
    planilha_nome: str | None
    anexos_count: int = 0
    observacao: str | None = None


class TriagemListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    data: list[TriagemRow]
```

- [ ] **Step 4: Serviço `triagem.py`**

Criar `apps/api/app/modules/licitacoes/triagem.py`:

```python
"""Consolidacao da Aba de Triagem (Captador Squad 2).

Uma query paginada sobre `licitacoes` + 4 lookups em lote (edital,
pasta, planilha principal, ultima observacao) -- sem N+1 por linha.
Criterio de UAT do cliente: os links devem abrir pasta e planilha
diretamente, sem navegacao manual.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import (
    DecisaoTriagem,
    Edital,
    Licitacao,
    PastaProjeto,
    PlanilhaOrcamentaria,
)
from app.modules.licitacoes.schemas import TriagemRow


def _link_portal(lic: Licitacao) -> str | None:
    if lic.orgao_cnpj and lic.ano_compra and lic.sequencial_compra:
        return (
            "https://pncp.gov.br/app/editais/"
            f"{lic.orgao_cnpj}/{lic.ano_compra}/{lic.sequencial_compra}"
        )
    return None


async def montar_triagem(
    db: AsyncSession,
    *,
    status: str | None = None,
    uf: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TriagemRow], int]:
    base = select(Licitacao)
    if status:
        base = base.where(Licitacao.status_triagem == status)
    if uf:
        base = base.where(Licitacao.uf_sigla == uf.upper())

    total = (
        await db.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()

    licitacoes = list(
        (
            await db.execute(
                base.order_by(Licitacao.data_publicacao_pncp.desc().nullslast())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    if not licitacoes:
        return [], int(total)

    ids = [lic.id for lic in licitacoes]

    editais = {
        e.licitacao_id: e
        for e in (
            await db.execute(select(Edital).where(Edital.licitacao_id.in_(ids)))
        ).scalars()
    }
    pastas = {
        p.licitacao_id: p
        for p in (
            await db.execute(
                select(PastaProjeto).where(PastaProjeto.licitacao_id.in_(ids))
            )
        ).scalars()
    }
    principais = {
        p.licitacao_id: p
        for p in (
            await db.execute(
                select(PlanilhaOrcamentaria).where(
                    PlanilhaOrcamentaria.licitacao_id.in_(ids),
                    PlanilhaOrcamentaria.principal,
                )
            )
        ).scalars()
    }
    # Ultima observacao nao-nula por licitacao (id mais alto = mais recente).
    observacoes: dict[int, str] = {}
    decisoes = (
        await db.execute(
            select(DecisaoTriagem)
            .where(
                DecisaoTriagem.licitacao_id.in_(ids),
                DecisaoTriagem.observacao.is_not(None),
            )
            .order_by(DecisaoTriagem.id)
        )
    ).scalars()
    for d in decisoes:
        observacoes[d.licitacao_id] = d.observacao

    rows: list[TriagemRow] = []
    for lic in licitacoes:
        edital = editais.get(lic.id)
        pasta = pastas.get(lic.id)
        planilha = principais.get(lic.id)
        rows.append(
            TriagemRow(
                licitacao_id=lic.id,
                status_triagem=lic.status_triagem,
                uf_sigla=lic.uf_sigla,
                municipio_nome=lic.municipio_nome,
                orgao_razao_social=lic.orgao_razao_social,
                objeto_compra=lic.objeto_compra,
                modalidade_nome=lic.modalidade_nome,
                valor_total_estimado=lic.valor_total_estimado,
                data_publicacao_pncp=lic.data_publicacao_pncp,
                link_portal=_link_portal(lic),
                link_pasta=pasta.link_pasta if pasta else None,
                link_planilha=planilha.link if planilha else None,
                planilha_nome=planilha.nome_arquivo if planilha else None,
                anexos_count=edital.anexos_count if edital else 0,
                observacao=observacoes.get(lic.id),
            )
        )
    return rows, int(total)
```

- [ ] **Step 5: Rota no router (ANTES de `/{licitacao_id}`)**

Em `apps/api/app/modules/licitacoes/router.py`, IMEDIATAMENTE ANTES de `@router.get("/{licitacao_id}", ...)` (linha ~176 — a ordem importa: declarada depois, `/triagem` seria capturada por `/{licitacao_id}` e devolveria 422):

```python
@router.get("/triagem", response_model=TriagemListResponse)
async def triagem_endpoint(
    status_triagem: str | None = Query(None, alias="status", max_length=32),
    uf: str | None = Query(None, max_length=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> TriagemListResponse:
    """Aba de Triagem consolidada: status + links diretos (pasta, planilha)."""
    rows, total = await montar_triagem(
        db, status=status_triagem, uf=uf, page=page, page_size=page_size
    )
    return TriagemListResponse(
        total=total, page=page, page_size=page_size, data=rows
    )
```

Imports a adicionar: `from app.modules.licitacoes.triagem import montar_triagem` e `TriagemListResponse` no bloco de schemas.

- [ ] **Step 6: Rodar até passar + suíte + lint**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v && uv run ruff check . && uv run --extra dev pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/triagem.py \
  apps/api/app/modules/licitacoes/router.py \
  apps/api/app/modules/licitacoes/schemas.py \
  apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): endpoint GET /licitacoes/triagem com links diretos"
```

---

### Task 8: Página web da Aba de Triagem

**Files:**
- Create: `apps/web/src/app/(dashboard)/licitacoes/triagem/page.tsx`
- Modify: `apps/web/src/app/(dashboard)/licitacoes/page.tsx` (adicionar link "Aba de Triagem" no topo — um `<Link href="/licitacoes/triagem">` junto aos links existentes de boletins/certidões)

**Interfaces:**
- Consumes: `GET /api/v1/licitacoes/triagem` (Task 7), `POST /api/v1/licitacoes/{id}/processar-anexos` (Task 6), `apiFetch` de `@/lib/api`.
- Produces: rota `/licitacoes/triagem` no app web.

- [ ] **Step 1: Implementar a página**

Criar `apps/web/src/app/(dashboard)/licitacoes/triagem/page.tsx`:

```tsx
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type TriagemRow = {
  licitacao_id: number;
  status_triagem: string;
  uf_sigla: string | null;
  municipio_nome: string | null;
  orgao_razao_social: string | null;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  valor_total_estimado: string | null;
  data_publicacao_pncp: string | null;
  link_portal: string | null;
  link_pasta: string | null;
  link_planilha: string | null;
  planilha_nome: string | null;
  anexos_count: number;
  observacao: string | null;
};

type TriagemResponse = {
  total: number;
  page: number;
  page_size: number;
  data: TriagemRow[];
};

export const dynamic = "force-dynamic";

// Critério de UAT do cliente: alerta VERMELHO quando a planilha
// orçamentária não foi localizada (status sem_planilha) e nos erros.
const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  novo_captado: { label: "Novo Captado", className: "bg-slate-100 text-slate-700" },
  em_analise: { label: "Em Análise", className: "bg-amber-100 text-amber-800" },
  aprovado: { label: "Aprovado", className: "bg-blue-100 text-blue-800" },
  rejeitado: { label: "Rejeitado", className: "bg-slate-200 text-slate-500" },
  processando_anexos: {
    label: "Processando Anexos",
    className: "bg-blue-100 text-blue-800",
  },
  completo: { label: "Completo", className: "bg-emerald-100 text-emerald-800" },
  sem_planilha: { label: "Sem Planilha", className: "bg-red-100 text-red-800" },
  erro_portal: { label: "Erro de Portal", className: "bg-red-100 text-red-800" },
  erro_sharepoint: {
    label: "Erro SharePoint",
    className: "bg-red-100 text-red-800",
  },
};

async function fetchTriagem(params: URLSearchParams): Promise<TriagemResponse | null> {
  try {
    return await apiFetch<TriagemResponse>(
      `/api/v1/licitacoes/triagem?${params.toString()}`,
    );
  } catch {
    return null;
  }
}

async function reprocessar(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/processar-anexos`, {
      method: "POST",
    });
  } catch (err) {
    console.error("[triagem] reprocessamento falhou", err);
  }
  revalidatePath("/licitacoes/triagem");
}

function formatCurrency(value: string | null): string {
  if (!value) return "—";
  const num = Number(value);
  if (Number.isNaN(num)) return value;
  return num.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("pt-BR");
}

export default async function TriagemPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const params = new URLSearchParams();
  const status = typeof searchParams.status === "string" ? searchParams.status : "";
  const uf = typeof searchParams.uf === "string" ? searchParams.uf : "";
  const page = typeof searchParams.page === "string" ? searchParams.page : "1";
  if (status) params.set("status", status);
  if (uf) params.set("uf", uf);
  params.set("page", page);
  params.set("page_size", "50");

  const triagem = await fetchTriagem(params);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/licitacoes" className="text-sm text-slate-500 hover:underline">
            ← Licitações
          </Link>
          <h1 className="text-xl font-semibold">Aba de Triagem</h1>
          <p className="text-sm text-slate-500">
            Editais aprovados com links diretos para pasta do projeto e
            planilha orçamentária.
          </p>
        </div>
        <form className="flex items-end gap-2">
          <label className="text-sm">
            <span className="block text-slate-500">Status</span>
            <select
              name="status"
              defaultValue={status}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">Todos</option>
              {Object.entries(STATUS_BADGE).map(([value, meta]) => (
                <option key={value} value={value}>
                  {meta.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-slate-500">UF</span>
            <input
              name="uf"
              defaultValue={uf}
              maxLength={2}
              className="w-14 rounded border border-slate-300 px-2 py-1 text-sm uppercase"
            />
          </label>
          <button
            type="submit"
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
          >
            Filtrar
          </button>
        </form>
      </div>

      {!triagem ? (
        <p className="text-sm text-red-600">
          Não foi possível carregar a triagem — API indisponível.
        </p>
      ) : triagem.data.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhuma licitação encontrada.</p>
      ) : (
        <div className="overflow-x-auto rounded border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Município / UF</th>
                <th className="px-3 py-2">Órgão</th>
                <th className="px-3 py-2">Objeto</th>
                <th className="px-3 py-2">Valor estimado</th>
                <th className="px-3 py-2">Publicação</th>
                <th className="px-3 py-2">Links</th>
                <th className="px-3 py-2">Observação</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {triagem.data.map((row) => {
                const badge =
                  STATUS_BADGE[row.status_triagem] ?? {
                    label: row.status_triagem,
                    className: "bg-slate-100 text-slate-700",
                  };
                const canReprocess =
                  row.status_triagem === "aprovado" ||
                  row.status_triagem.startsWith("erro") ||
                  row.status_triagem === "sem_planilha";
                return (
                  <tr key={row.licitacao_id} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <span
                        className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${badge.className}`}
                      >
                        {badge.label}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {row.municipio_nome ?? "—"} / {row.uf_sigla ?? "—"}
                    </td>
                    <td className="max-w-48 truncate px-3 py-2">
                      {row.orgao_razao_social ?? "—"}
                    </td>
                    <td className="max-w-64 truncate px-3 py-2">
                      {row.objeto_compra ?? "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatCurrency(row.valor_total_estimado)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatDate(row.data_publicacao_pncp)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex gap-2">
                        {row.link_portal ? (
                          <a
                            href={row.link_portal}
                            target="_blank"
                            rel="noreferrer"
                            className="text-blue-700 hover:underline"
                          >
                            Edital
                          </a>
                        ) : null}
                        <Link
                          href={
                            row.link_pasta ?? `/licitacoes/${row.licitacao_id}`
                          }
                          target={row.link_pasta ? "_blank" : undefined}
                          className="text-blue-700 hover:underline"
                        >
                          Pasta ({row.anexos_count})
                        </Link>
                        {row.link_planilha ? (
                          <a
                            href={row.link_planilha}
                            target="_blank"
                            rel="noreferrer"
                            className="font-medium text-emerald-700 hover:underline"
                            title={row.planilha_nome ?? undefined}
                          >
                            Planilha
                          </a>
                        ) : (
                          <span className="font-medium text-red-600">
                            Sem planilha
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="max-w-48 truncate px-3 py-2 text-slate-500">
                      {row.observacao ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      {canReprocess ? (
                        <form action={reprocessar}>
                          <input
                            type="hidden"
                            name="licitacao_id"
                            value={row.licitacao_id}
                          />
                          <button
                            type="submit"
                            className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                          >
                            Processar anexos
                          </button>
                        </form>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

Ajuste as classes utilitárias ao design system do repo se a página `/licitacoes` usar componentes de `@/components/ui` (confira o arquivo vizinho e siga o padrão dele — a estrutura acima usa as mesmas classes Tailwind da listagem existente).

- [ ] **Step 2: Link de navegação na listagem**

Em `apps/web/src/app/(dashboard)/licitacoes/page.tsx`, junto aos links de topo existentes (boletins/certidões — procure por `href="/licitacoes/boletins"`), adicionar:

```tsx
<Link href="/licitacoes/triagem" className="text-sm text-blue-700 hover:underline">
  Aba de Triagem
</Link>
```

- [ ] **Step 3: Lint + build**

Run: `cd apps/web && npm run lint && npm run build`
Expected: sem erros.

- [ ] **Step 4: Commit**

```bash
git add "apps/web/src/app/(dashboard)/licitacoes/triagem/page.tsx" \
  "apps/web/src/app/(dashboard)/licitacoes/page.tsx"
git commit -m "feat(captador): pagina Aba de Triagem com hiperlinks diretos e alerta vermelho"
```

---

## Self-Review (executada na escrita do plano)

- **Spec coverage:** task Celery + endpoint manual (Task 6); status machine completa (Task 5); pasta via storage abstraction (Tasks 3–5); scoring seção 8 com XLSX/XLS/ODS + termos com/sem acento + conteúdo interno (Task 1); `PATCH` para a analista trocar a principal (Tasks 5–6); `sem_planilha` + alerta vermelho (Tasks 5, 8); Aba de Triagem consolidada com hiperlinks (Tasks 7–8); migration com aviso de `down_revision` (Task 2). ✓
- **Placeholder scan:** nenhum TBD/TODO; todos os steps têm código completo. ✓
- **Type consistency:** `ensure_project_folder(*, licitacao_id: int, nome_pasta: str) -> tuple[str, str | None]` idêntico em protocol/Local/OneDrive/testes; `ProcessamentoResult` igual em schema/serviço/router; constantes `STATUS_*` definidas na Task 5 e importadas nas 6–8; `TriagemRow` igual em schema/serviço/página web. ✓
- **Gap conhecido e aceito:** conteúdo de `.xls` legado não pontua (registrado em Decisões); fallback ComprasNet/Licitações-e fora de escopo (scaffold existente, mesma posição do D.4).
