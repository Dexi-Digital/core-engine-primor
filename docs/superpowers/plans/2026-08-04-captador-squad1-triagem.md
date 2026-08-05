# Captador Squad 1 — Workflow de Triagem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar o workflow de triagem humana (aprovar / rejeitar / observar, com máquina de status e trilha de auditoria) ao módulo `licitacoes`, mais a Tela de Captação no web app.

**Architecture:** Extensão do módulo existente `apps/api/app/modules/licitacoes/` seguindo os padrões do repo: domínio+serviço em um arquivo novo (`triagem.py`, mesmo formato de `certidoes.py`), model novo + coluna nova em `models.py`, migration Alembic, endpoints no `router.py` existente, audit_log em toda mutação com `actor=current_user.email`. UI em Next.js App Router com Server Components + Server Actions (padrão da página `/licitacoes` atual). Ao aprovar, se `CAPTADOR_AUTO_PROCESS=1`, despacha a task Celery `worker.tasks.licitacoes.processar_edital_aprovado` (entregue pela Squad 2) — o dispatch nunca pode derrubar o aprovar.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + pytest(-asyncio, SQLite in-memory) no backend; Next.js 15 App Router + Tailwind no front. Comandos rodam com `uv run --extra dev` a partir de `apps/api`.

## Global Constraints

- Máquina de status (PDF do cliente): `novo_captado`, `em_analise`, `aprovado`, `rejeitado`, `processando_anexos`, `completo`, `sem_planilha`, `erro_portal`, `erro_sharepoint`.
- Toda mutação grava `audit_log` com o email do usuário autenticado (padrão AGENTS.md; ver `certidoes.py:_record_audit`).
- Rejeição exige motivo (mín. 5 caracteres). Aprovar/rejeitar só a partir de `novo_captado`/`em_analise`.
- Task Celery da Squad 2 chama-se exatamente `worker.tasks.licitacoes.processar_edital_aprovado`, args `[licitacao_id]`, fila `licitacoes`. Flag `captador_auto_process` default **False**.
- Migration: o `down_revision` abaixo usa o head vigente em 2026-08-04 (`b8c9d0e1f2a3`). **Outras squads também criam migrations** — na hora de implementar, rode `cd apps/api && uv run --extra dev alembic heads` e ajuste `down_revision` para o head real, mantendo linha única.
- Testes backend: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v`. Lint: `uv run --extra dev ruff check .` (deve ficar limpo antes de cada commit).
- Front: `cd apps/web && npm run lint && npx tsc --noEmit`.

---

### Task 1: Máquina de status (domínio puro)

**Files:**
- Create: `apps/api/app/modules/licitacoes/triagem.py`
- Test: `apps/api/tests/test_licitacoes_triagem.py`

**Interfaces:**
- Consumes: nada (domínio puro, sem DB).
- Produces (usado pelas Tasks 3–5 e pela Squad 2):
  - Constantes `STATUS_NOVO_CAPTADO = "novo_captado"`, `STATUS_EM_ANALISE = "em_analise"`, `STATUS_APROVADO = "aprovado"`, `STATUS_REJEITADO = "rejeitado"`, `STATUS_PROCESSANDO_ANEXOS = "processando_anexos"`, `STATUS_COMPLETO = "completo"`, `STATUS_SEM_PLANILHA = "sem_planilha"`, `STATUS_ERRO_PORTAL = "erro_portal"`, `STATUS_ERRO_SHAREPOINT = "erro_sharepoint"`; `STATUS_VALIDOS: frozenset[str]`.
  - `DECISAO_APROVADO = "aprovado"`, `DECISAO_REJEITADO = "rejeitado"`, `DECISAO_OBSERVACAO = "observacao"`.
  - `class TransicaoInvalidaError(ValueError)` com atributos `.atual` e `.novo`.
  - `validar_transicao(atual: str, novo: str) -> None` (levanta `TransicaoInvalidaError`).

- [ ] **Step 1: Escrever os testes que falham**

Criar `apps/api/tests/test_licitacoes_triagem.py`:

```python
"""Tests do workflow de triagem do Captador (Squad 1)."""
from __future__ import annotations

import pytest

from app.modules.licitacoes import triagem


class TestMaquinaDeStatus:
    def test_status_validos_cobrem_pdf_do_cliente(self) -> None:
        assert triagem.STATUS_VALIDOS == frozenset(
            {
                "novo_captado",
                "em_analise",
                "aprovado",
                "rejeitado",
                "processando_anexos",
                "completo",
                "sem_planilha",
                "erro_portal",
                "erro_sharepoint",
            }
        )

    @pytest.mark.parametrize(
        ("atual", "novo"),
        [
            ("novo_captado", "em_analise"),
            ("novo_captado", "aprovado"),
            ("novo_captado", "rejeitado"),
            ("em_analise", "aprovado"),
            ("em_analise", "rejeitado"),
            ("em_analise", "novo_captado"),
            ("aprovado", "processando_anexos"),
            ("processando_anexos", "completo"),
            ("processando_anexos", "sem_planilha"),
            ("processando_anexos", "erro_portal"),
            ("processando_anexos", "erro_sharepoint"),
            ("erro_portal", "processando_anexos"),
            ("erro_sharepoint", "processando_anexos"),
            ("sem_planilha", "processando_anexos"),
        ],
    )
    def test_transicoes_permitidas(self, atual: str, novo: str) -> None:
        triagem.validar_transicao(atual, novo)  # nao levanta

    @pytest.mark.parametrize(
        ("atual", "novo"),
        [
            ("aprovado", "rejeitado"),  # aprovado nao pode ser rejeitado depois
            ("rejeitado", "aprovado"),  # rejeitado e terminal
            ("completo", "novo_captado"),  # completo e terminal
            ("novo_captado", "completo"),  # nao pula a aprovacao
            ("novo_captado", "processando_anexos"),
        ],
    )
    def test_transicoes_proibidas(self, atual: str, novo: str) -> None:
        with pytest.raises(triagem.TransicaoInvalidaError) as exc:
            triagem.validar_transicao(atual, novo)
        assert exc.value.atual == atual
        assert exc.value.novo == novo

    def test_status_desconhecido_levanta(self) -> None:
        with pytest.raises(triagem.TransicaoInvalidaError):
            triagem.validar_transicao("banana", "aprovado")
        with pytest.raises(triagem.TransicaoInvalidaError):
            triagem.validar_transicao("novo_captado", "banana")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (`triagem` não existe).

- [ ] **Step 3: Implementar o domínio**

Criar `apps/api/app/modules/licitacoes/triagem.py`:

```python
"""Workflow de triagem do Captador de Licitacoes (Squad 1).

Maquina de status vinda do Projeto Tecnico do cliente (23/06/2026):
a analista aprova/rejeita/observa editais captados; a aprovacao dispara
(o processamento da Squad 2: pasta + anexos + planilha orcamentaria).

Este arquivo segue o formato de `certidoes.py`: constantes de dominio +
funcoes de servico async no mesmo modulo.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# --- Maquina de status (secao 5.1 do Projeto Tecnico) ----------------------

STATUS_NOVO_CAPTADO = "novo_captado"
STATUS_EM_ANALISE = "em_analise"
STATUS_APROVADO = "aprovado"
STATUS_REJEITADO = "rejeitado"
STATUS_PROCESSANDO_ANEXOS = "processando_anexos"
STATUS_COMPLETO = "completo"
STATUS_SEM_PLANILHA = "sem_planilha"
STATUS_ERRO_PORTAL = "erro_portal"
STATUS_ERRO_SHAREPOINT = "erro_sharepoint"

STATUS_VALIDOS: frozenset[str] = frozenset(
    {
        STATUS_NOVO_CAPTADO,
        STATUS_EM_ANALISE,
        STATUS_APROVADO,
        STATUS_REJEITADO,
        STATUS_PROCESSANDO_ANEXOS,
        STATUS_COMPLETO,
        STATUS_SEM_PLANILHA,
        STATUS_ERRO_PORTAL,
        STATUS_ERRO_SHAREPOINT,
    }
)

# Decisoes registradas na trilha (`licitacoes_decisoes_triagem.decisao`).
DECISAO_APROVADO = "aprovado"
DECISAO_REJEITADO = "rejeitado"
DECISAO_OBSERVACAO = "observacao"

# De onde se pode ir para onde. `rejeitado` e `completo` sao terminais;
# os `erro_*` e `sem_planilha` permitem reprocessar (Squad 2 re-dispara).
TRANSICOES_VALIDAS: dict[str, frozenset[str]] = {
    STATUS_NOVO_CAPTADO: frozenset(
        {STATUS_EM_ANALISE, STATUS_APROVADO, STATUS_REJEITADO}
    ),
    STATUS_EM_ANALISE: frozenset(
        {STATUS_APROVADO, STATUS_REJEITADO, STATUS_NOVO_CAPTADO}
    ),
    STATUS_APROVADO: frozenset(
        {STATUS_PROCESSANDO_ANEXOS, STATUS_ERRO_PORTAL, STATUS_ERRO_SHAREPOINT}
    ),
    STATUS_PROCESSANDO_ANEXOS: frozenset(
        {
            STATUS_COMPLETO,
            STATUS_SEM_PLANILHA,
            STATUS_ERRO_PORTAL,
            STATUS_ERRO_SHAREPOINT,
        }
    ),
    STATUS_ERRO_PORTAL: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_ERRO_SHAREPOINT: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_SEM_PLANILHA: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_REJEITADO: frozenset(),
    STATUS_COMPLETO: frozenset(),
}


class TransicaoInvalidaError(ValueError):
    """Transicao de status nao permitida pela maquina de triagem."""

    def __init__(self, atual: str, novo: str) -> None:
        self.atual = atual
        self.novo = novo
        super().__init__(
            f"Transicao de triagem invalida: {atual!r} -> {novo!r}"
        )


def validar_transicao(atual: str, novo: str) -> None:
    """Levanta `TransicaoInvalidaError` se `atual -> novo` nao for permitido."""
    if atual not in STATUS_VALIDOS or novo not in STATUS_VALIDOS:
        raise TransicaoInvalidaError(atual, novo)
    if novo not in TRANSICOES_VALIDAS[atual]:
        raise TransicaoInvalidaError(atual, novo)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v`
Expected: PASS (todos os testes de `TestMaquinaDeStatus`).

- [ ] **Step 5: Lint + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/licitacoes/triagem.py apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): maquina de status da triagem de editais (Squad 1)"
```

---

### Task 2: Coluna `status_triagem` + model `DecisaoTriagem` + migration

**Files:**
- Modify: `apps/api/app/modules/licitacoes/models.py` (classe `Licitacao`, após o campo `raw`, ~linha 68; e nova classe no fim do bloco de models do PNCP, após `AnexoEdital`)
- Create: `apps/api/alembic/versions/a1b2c3d4e5f6_captador_triagem.py`
- Test: `apps/api/tests/test_licitacoes_triagem.py` (append)

**Interfaces:**
- Consumes: constantes de `triagem.py` (Task 1) — apenas como documentação dos valores válidos.
- Produces:
  - `Licitacao.status_triagem: Mapped[str]` (String(32), default e server_default `"novo_captado"`, index).
  - `class DecisaoTriagem(Base)`, tabela `licitacoes_decisoes_triagem`, campos: `id: int`, `licitacao_id: int` (FK `licitacoes.id` ondelete CASCADE, index), `decisao: str` (String(16)), `observacao: str | None` (Text), `usuario_email: str` (String(255)), `created_at: datetime` (server_default now).

- [ ] **Step 1: Escrever o teste que falha**

Append em `apps/api/tests/test_licitacoes_triagem.py`:

```python
from sqlalchemy import select

from app.modules.licitacoes.models import DecisaoTriagem, Licitacao


def _mk_licitacao(external_id: str = "trg-1") -> Licitacao:
    return Licitacao(
        external_id=external_id,
        source="pncp",
        objeto_compra="Pavimentacao asfaltica em vias urbanas",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
        modalidade_nome="Pregao Eletronico",
    )


@pytest.mark.asyncio
async def test_licitacao_nasce_novo_captado_e_decisao_persiste(db_session) -> None:
    lic = _mk_licitacao()
    db_session.add(lic)
    await db_session.commit()
    assert lic.status_triagem == "novo_captado"

    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id,
            decisao="aprovado",
            observacao=None,
            usuario_email="analista@primor.com",
        )
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(DecisaoTriagem).where(DecisaoTriagem.licitacao_id == lic.id)
    )
    assert row is not None
    assert row.decisao == "aprovado"
    assert row.usuario_email == "analista@primor.com"
    assert row.created_at is not None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v -k decisao_persiste`
Expected: FAIL — `ImportError: cannot import name 'DecisaoTriagem'`.

- [ ] **Step 3: Adicionar coluna e model**

Em `apps/api/app/modules/licitacoes/models.py`, dentro de `Licitacao`, logo após o campo `raw` (~linha 68):

```python
    # --- Captador Squad 1: triagem humana (ver triagem.py) -----------------
    # Valores validos em `triagem.STATUS_VALIDOS`; string livre no schema
    # para permitir novos status sem migracao (mesmo racional de
    # `CertidaoEmpresa.tipo`).
    status_triagem: Mapped[str] = mapped_column(
        String(32),
        default="novo_captado",
        server_default="novo_captado",
        index=True,
    )
```

No mesmo arquivo, nova classe após `AnexoEdital` (antes de `EditalAnalise`):

```python
class DecisaoTriagem(Base):
    """Trilha de decisoes da triagem humana do Captador (Squad 1).

    Uma row por acao da analista (aprovar / rejeitar / observacao).
    O motivo de rejeicao e obrigatorio na camada de servico; aqui a
    coluna e nullable porque aprovacao/observacao podem vir sem texto.
    """

    __tablename__ = "licitacoes_decisoes_triagem"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        index=True,
    )
    # 'aprovado' | 'rejeitado' | 'observacao' (triagem.DECISAO_*)
    decisao: Mapped[str] = mapped_column(String(16))
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    usuario_email: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_decisoes_triagem_lic_criado", "licitacao_id", "created_at"),
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v`
Expected: PASS.

- [ ] **Step 5: Escrever a migration**

Antes: `cd apps/api && uv run --extra dev alembic heads` — se o head não for `b8c9d0e1f2a3`, use o head impresso como `down_revision`.

Criar `apps/api/alembic/versions/a1b2c3d4e5f6_captador_triagem.py`:

```python
"""captador triagem: status_triagem em licitacoes + decisoes (Squad 1)

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
    # Editais ja ingeridos nascem 'novo_captado' (server_default cobre o
    # backfill). Maquina de status completa em licitacoes/triagem.py.
    op.add_column(
        "licitacoes",
        sa.Column(
            "status_triagem",
            sa.String(length=32),
            nullable=False,
            server_default="novo_captado",
        ),
    )
    op.create_index(
        "ix_licitacoes_status_triagem", "licitacoes", ["status_triagem"]
    )

    op.create_table(
        "licitacoes_decisoes_triagem",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'aprovado' | 'rejeitado' | 'observacao'
        sa.Column("decisao", sa.String(length=16), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("usuario_email", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_licitacoes_decisoes_triagem_licitacao_id",
        "licitacoes_decisoes_triagem",
        ["licitacao_id"],
    )
    op.create_index(
        "ix_decisoes_triagem_lic_criado",
        "licitacoes_decisoes_triagem",
        ["licitacao_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decisoes_triagem_lic_criado",
        table_name="licitacoes_decisoes_triagem",
    )
    op.drop_index(
        "ix_licitacoes_decisoes_triagem_licitacao_id",
        table_name="licitacoes_decisoes_triagem",
    )
    op.drop_table("licitacoes_decisoes_triagem")
    op.drop_index("ix_licitacoes_status_triagem", table_name="licitacoes")
    op.drop_column("licitacoes", "status_triagem")
```

- [ ] **Step 6: Validar suite completa + lint**

Run: `cd apps/api && uv run --extra dev pytest tests/ -q && uv run --extra dev ruff check .`
Expected: PASS (nenhum teste existente quebra — a coluna nova tem default) e ruff limpo.
Opcional (se o Postgres local do docker-compose estiver de pé): `uv run --extra dev --with greenlet alembic upgrade head` e depois `alembic downgrade -1` + `upgrade head` para validar o ciclo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/licitacoes/models.py apps/api/alembic/versions/a1b2c3d4e5f6_captador_triagem.py apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): status_triagem + trilha de decisoes (model e migration)"
```

---

### Task 3: Serviço de triagem (aprovar / rejeitar / observar) com audit e dispatch Celery

**Files:**
- Modify: `apps/api/app/modules/licitacoes/triagem.py` (append ao arquivo da Task 1)
- Modify: `apps/api/app/core/config.py` (classe `Settings`, junto ao bloco `storage_backend`, ~linha 87)
- Test: `apps/api/tests/test_licitacoes_triagem.py` (append)

**Interfaces:**
- Consumes: `validar_transicao`, constantes (Task 1); `DecisaoTriagem`, `Licitacao.status_triagem` (Task 2); `AuditLog` de `app.audit.models`; `get_settings` de `app.core.config`.
- Produces (usado pela Task 4):
  - `async def aprovar(db: AsyncSession, *, licitacao_id: int, usuario_email: str, observacao: str | None = None) -> DecisaoTriagem` — levanta `LookupError` (não achou) e `TransicaoInvalidaError`.
  - `async def rejeitar(db: AsyncSession, *, licitacao_id: int, usuario_email: str, observacao: str) -> DecisaoTriagem` — mesmas exceções; `ValueError` se observacao vazia/curta (<5 chars após strip).
  - `async def registrar_observacao(db: AsyncSession, *, licitacao_id: int, usuario_email: str, observacao: str) -> DecisaoTriagem` — move `novo_captado -> em_analise` de carona; `ValueError` se observacao vazia.
  - `async def listar_decisoes(db: AsyncSession, *, licitacao_id: int) -> list[DecisaoTriagem]` — mais recente primeiro.
  - `Settings.captador_auto_process: bool` (env `CAPTADOR_AUTO_PROCESS`, default False).
  - `reset_celery_dispatcher()` para testes.

- [ ] **Step 1: Escrever os testes que falham**

Append em `apps/api/tests/test_licitacoes_triagem.py`:

```python
from app.audit.models import AuditLog


@pytest.mark.asyncio
async def test_aprovar_muda_status_grava_decisao_e_audit(db_session) -> None:
    lic = _mk_licitacao("trg-aprovar")
    db_session.add(lic)
    await db_session.commit()

    decisao = await triagem.aprovar(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="dentro do perfil de engenharia",
    )

    assert decisao.decisao == "aprovado"
    await db_session.refresh(lic)
    assert lic.status_triagem == "aprovado"

    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.resource == "licitacoes.triagem")
    )
    assert audit is not None
    assert audit.actor == "analista@primor.com"
    assert audit.action == "triagem.aprovar"
    assert audit.resource_id == str(lic.id)


@pytest.mark.asyncio
async def test_rejeitar_exige_motivo(db_session) -> None:
    lic = _mk_licitacao("trg-rejeitar-sem-motivo")
    db_session.add(lic)
    await db_session.commit()

    with pytest.raises(ValueError, match="motivo"):
        await triagem.rejeitar(
            db_session,
            licitacao_id=lic.id,
            usuario_email="analista@primor.com",
            observacao="   ",
        )
    await db_session.refresh(lic)
    assert lic.status_triagem == "novo_captado"


@pytest.mark.asyncio
async def test_rejeitar_com_motivo_e_terminal(db_session) -> None:
    lic = _mk_licitacao("trg-rejeitar")
    db_session.add(lic)
    await db_session.commit()

    await triagem.rejeitar(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="objeto fora do perfil (merenda escolar)",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "rejeitado"

    with pytest.raises(triagem.TransicaoInvalidaError):
        await triagem.aprovar(
            db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
        )


@pytest.mark.asyncio
async def test_observacao_move_para_em_analise(db_session) -> None:
    lic = _mk_licitacao("trg-obs")
    db_session.add(lic)
    await db_session.commit()

    await triagem.registrar_observacao(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="aguardando planilha no portal",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "em_analise"

    # segunda observacao nao muda mais o status
    await triagem.registrar_observacao(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="portal voltou",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "em_analise"

    rows = await triagem.listar_decisoes(db_session, licitacao_id=lic.id)
    assert len(rows) == 2
    assert rows[0].observacao == "portal voltou"  # mais recente primeiro


@pytest.mark.asyncio
async def test_aprovar_licitacao_inexistente(db_session) -> None:
    with pytest.raises(LookupError):
        await triagem.aprovar(
            db_session, licitacao_id=99999, usuario_email="a@primor.com"
        )


@pytest.mark.asyncio
async def test_aprovar_dispara_celery_quando_flag_ligada(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("CAPTADOR_AUTO_PROCESS", "1")
    get_settings.cache_clear()

    enviados: list[tuple[str, list, str]] = []

    class FakeDispatcher:
        def send_task(self, name: str, args: list, queue: str) -> None:
            enviados.append((name, args, queue))

    monkeypatch.setattr(triagem, "_get_celery_dispatcher", lambda: FakeDispatcher())

    lic = _mk_licitacao("trg-celery")
    db_session.add(lic)
    await db_session.commit()
    await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )

    assert enviados == [
        ("worker.tasks.licitacoes.processar_edital_aprovado", [lic.id], "licitacoes")
    ]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_aprovar_sobrevive_broker_fora(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem a task da Squad 2 registrada / broker down, aprovar NAO pode falhar."""
    from app.core.config import get_settings

    monkeypatch.setenv("CAPTADOR_AUTO_PROCESS", "1")
    get_settings.cache_clear()

    def _boom() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(triagem, "_get_celery_dispatcher", _boom)

    lic = _mk_licitacao("trg-broker-down")
    db_session.add(lic)
    await db_session.commit()
    decisao = await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )
    assert decisao.decisao == "aprovado"
    await db_session.refresh(lic)
    assert lic.status_triagem == "aprovado"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_aprovar_nao_dispara_celery_por_default(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamado: list[bool] = []
    monkeypatch.setattr(
        triagem, "_get_celery_dispatcher", lambda: chamado.append(True)
    )
    lic = _mk_licitacao("trg-sem-flag")
    db_session.add(lic)
    await db_session.commit()
    await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )
    assert chamado == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v -k "aprovar or rejeitar or observacao"`
Expected: FAIL — `AttributeError: module ... has no attribute 'aprovar'`.

- [ ] **Step 3: Adicionar a flag em Settings**

Em `apps/api/app/core/config.py`, na classe `Settings`, logo abaixo de `storage_backend` (~linha 87):

```python
    # Captador Squad 1: quando True, aprovar um edital na triagem despacha
    # a task Celery `worker.tasks.licitacoes.processar_edital_aprovado` (Squad 2).
    # Default False ate a Squad 2 registrar a task no worker.
    captador_auto_process: bool = Field(default=False)
```

- [ ] **Step 4: Implementar o serviço**

Append em `apps/api/app/modules/licitacoes/triagem.py`:

```python
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.config import get_settings
from app.modules.licitacoes.models import DecisaoTriagem, Licitacao

_AUDIT_RESOURCE = "licitacoes.triagem"

MOTIVO_REJEICAO_MIN_CHARS = 5

# --- Celery dispatch (Squad 2) ---------------------------------------------
# Singleton por processo, mesmo racional de
# `manutencao_frota.service.get_celery_dispatcher` (evita vazar pool de
# conexoes broker a cada aprovacao).
_celery_dispatcher: Any | None = None


def _get_celery_dispatcher() -> Any:
    global _celery_dispatcher
    if _celery_dispatcher is None:
        from celery import Celery

        _celery_dispatcher = Celery(broker=get_settings().redis_url)
    return _celery_dispatcher


def reset_celery_dispatcher() -> Any | None:
    """Limpa o singleton (testes / lifespan)."""
    global _celery_dispatcher
    prev = _celery_dispatcher
    _celery_dispatcher = None
    return prev


def _enqueue_processamento(licitacao_id: int) -> None:
    """Despacha o processamento da Squad 2 se a flag estiver ligada.

    A task pode nao existir ainda (Squad 2 em desenvolvimento) e o broker
    pode estar fora -- em NENHUM caso o aprovar pode falhar por isso.
    """
    if not get_settings().captador_auto_process:
        return
    try:
        _get_celery_dispatcher().send_task(
            "worker.tasks.licitacoes.processar_edital_aprovado",
            args=[licitacao_id],
            queue="licitacoes",
        )
    except Exception:  # noqa: BLE001 -- dispatch e best-effort por contrato
        logger.exception(
            "dispatch de worker.tasks.licitacoes.processar_edital_aprovado falhou "
            "(licitacao_id=%s); processar manualmente ou reaprovar",
            licitacao_id,
        )


# --- Servico ----------------------------------------------------------------


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int,
    actor: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id),
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )


async def _get_licitacao_or_raise(
    db: AsyncSession, licitacao_id: int
) -> Licitacao:
    lic = await db.scalar(select(Licitacao).where(Licitacao.id == licitacao_id))
    if lic is None:
        raise LookupError(f"Licitacao {licitacao_id} nao encontrada")
    return lic


async def _decidir(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    decisao: str,
    novo_status: str,
    observacao: str | None,
) -> DecisaoTriagem:
    lic = await _get_licitacao_or_raise(db, licitacao_id)
    validar_transicao(lic.status_triagem, novo_status)
    status_anterior = lic.status_triagem
    lic.status_triagem = novo_status
    row = DecisaoTriagem(
        licitacao_id=lic.id,
        decisao=decisao,
        observacao=observacao,
        usuario_email=usuario_email,
    )
    db.add(row)
    await _record_audit(
        db,
        action=f"triagem.{decisao}",
        resource_id=lic.id,
        actor=usuario_email,
        metadata={
            "status_anterior": status_anterior,
            "status_novo": novo_status,
            "observacao": observacao,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def aprovar(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    observacao: str | None = None,
) -> DecisaoTriagem:
    """Aprova o edital na triagem. Dispara a Squad 2 se a flag permitir."""
    row = await _decidir(
        db,
        licitacao_id=licitacao_id,
        usuario_email=usuario_email,
        decisao=DECISAO_APROVADO,
        novo_status=STATUS_APROVADO,
        observacao=observacao.strip() if observacao else None,
    )
    _enqueue_processamento(licitacao_id)
    return row


async def rejeitar(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    observacao: str,
) -> DecisaoTriagem:
    """Rejeita o edital. Motivo obrigatorio (secao 7.1 do Projeto Tecnico)."""
    motivo = (observacao or "").strip()
    if len(motivo) < MOTIVO_REJEICAO_MIN_CHARS:
        raise ValueError(
            "Rejeicao exige motivo com pelo menos "
            f"{MOTIVO_REJEICAO_MIN_CHARS} caracteres"
        )
    return await _decidir(
        db,
        licitacao_id=licitacao_id,
        usuario_email=usuario_email,
        decisao=DECISAO_REJEITADO,
        novo_status=STATUS_REJEITADO,
        observacao=motivo,
    )


async def registrar_observacao(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    observacao: str,
) -> DecisaoTriagem:
    """Anota uma observacao sem decidir. `novo_captado` vira `em_analise`."""
    texto = (observacao or "").strip()
    if not texto:
        raise ValueError("Observacao nao pode ser vazia")
    lic = await _get_licitacao_or_raise(db, licitacao_id)
    status_anterior = lic.status_triagem
    if lic.status_triagem == STATUS_NOVO_CAPTADO:
        lic.status_triagem = STATUS_EM_ANALISE
    row = DecisaoTriagem(
        licitacao_id=lic.id,
        decisao=DECISAO_OBSERVACAO,
        observacao=texto,
        usuario_email=usuario_email,
    )
    db.add(row)
    await _record_audit(
        db,
        action="triagem.observacao",
        resource_id=lic.id,
        actor=usuario_email,
        metadata={
            "status_anterior": status_anterior,
            "status_novo": lic.status_triagem,
            "observacao": texto,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def listar_decisoes(
    db: AsyncSession, *, licitacao_id: int
) -> list[DecisaoTriagem]:
    """Historico de decisoes, mais recente primeiro."""
    await _get_licitacao_or_raise(db, licitacao_id)
    rows = await db.scalars(
        select(DecisaoTriagem)
        .where(DecisaoTriagem.licitacao_id == licitacao_id)
        .order_by(DecisaoTriagem.created_at.desc(), DecisaoTriagem.id.desc())
    )
    return list(rows)
```

Nota: os imports novos (`json`, `Any`, `select`, `AsyncSession`, `AuditLog`, `get_settings`, models) devem subir para o topo do arquivo junto dos existentes — ruff (isort) acusa se ficarem no meio.

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v`
Expected: PASS (todos).

- [ ] **Step 6: Suite completa + lint + commit**

```bash
cd apps/api && uv run --extra dev pytest tests/ -q && uv run --extra dev ruff check .
git add apps/api/app/modules/licitacoes/triagem.py apps/api/app/core/config.py apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): servico de triagem com audit e dispatch p/ Squad 2"
```

---

### Task 4: Schemas + endpoints REST da triagem

**Files:**
- Modify: `apps/api/app/modules/licitacoes/schemas.py` (append no fim; e adicionar campo em `LicitacaoRead`, ~linha 11)
- Modify: `apps/api/app/modules/licitacoes/router.py` (novo bloco após D.5, ~linha 389)
- Test: `apps/api/tests/test_licitacoes_triagem.py` (append)

**Interfaces:**
- Consumes: `aprovar`, `rejeitar`, `registrar_observacao`, `listar_decisoes`, `TransicaoInvalidaError` (Task 3); fixtures `api_client`, `db_session`, `auth_headers` do conftest.
- Produces (usado pela Task 6/UI):
  - `POST /api/v1/licitacoes/{id}/triagem/aprovar` body `{"observacao": str|null}` → 200 `DecisaoTriagemRead`.
  - `POST /api/v1/licitacoes/{id}/triagem/rejeitar` body `{"observacao": str}` (min 5) → 200 `DecisaoTriagemRead`.
  - `POST /api/v1/licitacoes/{id}/triagem/observacao` body `{"observacao": str}` (min 1) → 200 `DecisaoTriagemRead`.
  - `GET /api/v1/licitacoes/{id}/triagem` → 200 `list[DecisaoTriagemRead]`.
  - Erros: 401 sem JWT (mutações), 404 licitação inexistente, 409 transição inválida, 422 payload inválido.
  - `LicitacaoRead.status_triagem: str`.

- [ ] **Step 1: Escrever os testes que falham**

Append em `apps/api/tests/test_licitacoes_triagem.py`:

```python
@pytest.mark.asyncio
async def test_endpoint_aprovar_e_historico(
    api_client, db_session, auth_headers
) -> None:
    lic = _mk_licitacao("trg-api-aprovar")
    db_session.add(lic)
    await db_session.commit()

    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/aprovar",
        json={"observacao": "perfil ok"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decisao"] == "aprovado"
    assert body["usuario_email"] == "test-admin@primor.com"

    r2 = await api_client.get(f"/api/v1/licitacoes/{lic.id}/triagem")
    assert r2.status_code == 200
    assert [d["decisao"] for d in r2.json()] == ["aprovado"]

    # status aparece no read da licitacao
    r3 = await api_client.get(f"/api/v1/licitacoes/{lic.id}")
    assert r3.json()["status_triagem"] == "aprovado"


@pytest.mark.asyncio
async def test_endpoint_aprovar_exige_jwt(api_client, db_session) -> None:
    lic = _mk_licitacao("trg-api-401")
    db_session.add(lic)
    await db_session.commit()
    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/aprovar", json={}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_rejeitar_sem_motivo_422(
    api_client, db_session, auth_headers
) -> None:
    lic = _mk_licitacao("trg-api-422")
    db_session.add(lic)
    await db_session.commit()
    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/rejeitar",
        json={"observacao": "ok"},  # < 5 chars
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_transicao_invalida_409(
    api_client, db_session, auth_headers
) -> None:
    lic = _mk_licitacao("trg-api-409")
    lic.status_triagem = "rejeitado"
    db_session.add(lic)
    await db_session.commit()
    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/aprovar",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert "rejeitado" in r.json()["detail"]


@pytest.mark.asyncio
async def test_endpoint_404(api_client, auth_headers) -> None:
    r = await api_client.post(
        "/api/v1/licitacoes/99999/triagem/aprovar",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 404
    r2 = await api_client.get("/api/v1/licitacoes/99999/triagem")
    assert r2.status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v -k endpoint`
Expected: FAIL — 404 nos POSTs (rota não existe) e `KeyError: 'status_triagem'`.

- [ ] **Step 3: Schemas**

Em `apps/api/app/modules/licitacoes/schemas.py`, adicionar em `LicitacaoRead` (após `source`):

```python
    status_triagem: str
```

E no fim do arquivo:

```python
# --- Captador Squad 1: triagem ---------------------------------------------


class TriagemAprovarPayload(BaseModel):
    observacao: str | None = Field(default=None, max_length=2000)


class TriagemRejeitarPayload(BaseModel):
    # Motivo obrigatorio -- espelha MOTIVO_REJEICAO_MIN_CHARS do servico.
    observacao: str = Field(min_length=5, max_length=2000)


class TriagemObservacaoPayload(BaseModel):
    observacao: str = Field(min_length=1, max_length=2000)


class DecisaoTriagemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    licitacao_id: int
    decisao: str
    observacao: str | None
    usuario_email: str
    created_at: datetime
```

- [ ] **Step 4: Endpoints**

Em `apps/api/app/modules/licitacoes/router.py`, adicionar aos imports de `app.modules.licitacoes.schemas`: `DecisaoTriagemRead, TriagemAprovarPayload, TriagemObservacaoPayload, TriagemRejeitarPayload`; e novo import:

```python
from app.modules.licitacoes import triagem
```

Novo bloco no fim do arquivo (após D.5):

```python
# --- Captador Squad 1: triagem ---


@router.post(
    "/{licitacao_id}/triagem/aprovar", response_model=DecisaoTriagemRead
)
async def triagem_aprovar_endpoint(
    licitacao_id: int,
    payload: TriagemAprovarPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisaoTriagemRead:
    """Aprova o edital na triagem (Tela de Captacao).

    Se `CAPTADOR_AUTO_PROCESS=1`, dispara o processamento da Squad 2
    (pasta + anexos + planilha) em background -- best-effort.
    """
    try:
        row = await triagem.aprovar(
            db,
            licitacao_id=licitacao_id,
            usuario_email=current_user.email,
            observacao=payload.observacao,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except triagem.TransicaoInvalidaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DecisaoTriagemRead.model_validate(row)


@router.post(
    "/{licitacao_id}/triagem/rejeitar", response_model=DecisaoTriagemRead
)
async def triagem_rejeitar_endpoint(
    licitacao_id: int,
    payload: TriagemRejeitarPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisaoTriagemRead:
    """Rejeita o edital com motivo obrigatorio (fica registrado na trilha)."""
    try:
        row = await triagem.rejeitar(
            db,
            licitacao_id=licitacao_id,
            usuario_email=current_user.email,
            observacao=payload.observacao,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except triagem.TransicaoInvalidaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DecisaoTriagemRead.model_validate(row)


@router.post(
    "/{licitacao_id}/triagem/observacao", response_model=DecisaoTriagemRead
)
async def triagem_observacao_endpoint(
    licitacao_id: int,
    payload: TriagemObservacaoPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisaoTriagemRead:
    """Registra observacao sem decidir; `novo_captado` passa a `em_analise`."""
    try:
        row = await triagem.registrar_observacao(
            db,
            licitacao_id=licitacao_id,
            usuario_email=current_user.email,
            observacao=payload.observacao,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DecisaoTriagemRead.model_validate(row)


@router.get(
    "/{licitacao_id}/triagem", response_model=list[DecisaoTriagemRead]
)
async def triagem_historico_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[DecisaoTriagemRead]:
    """Historico de decisoes da triagem, mais recente primeiro."""
    try:
        rows = await triagem.listar_decisoes(db, licitacao_id=licitacao_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [DecisaoTriagemRead.model_validate(r) for r in rows]
```

Atenção à ordem das rotas: os paths `/{licitacao_id}/triagem/...` são mais específicos que `/{licitacao_id}` (GET), mas o FastAPI resolve por método+path — não há conflito. Não mover o bloco para antes de `/boletins/...`.

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py tests/test_licitacoes_router.py -v`
Expected: PASS — inclusive os testes antigos do router (o campo novo em `LicitacaoRead` tem valor default via server_default do model).

- [ ] **Step 6: Suite completa + lint + commit**

```bash
cd apps/api && uv run --extra dev pytest tests/ -q && uv run --extra dev ruff check .
git add apps/api/app/modules/licitacoes/schemas.py apps/api/app/modules/licitacoes/router.py apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): endpoints REST da triagem (aprovar/rejeitar/observar/historico)"
```

---

### Task 5: Filtros `status_triagem` e `municipio` na listagem

**Files:**
- Modify: `apps/api/app/modules/licitacoes/service.py` (função `list_licitacoes`, após `ingest_publicacoes` — localizar com `grep -n "async def list_licitacoes" service.py`)
- Modify: `apps/api/app/modules/licitacoes/router.py:145-173` (`list_endpoint`)
- Test: `apps/api/tests/test_licitacoes_triagem.py` (append)

**Interfaces:**
- Consumes: `Licitacao.status_triagem` (Task 2).
- Produces (usado pela UI/Task 6): `GET /api/v1/licitacoes?status_triagem=novo_captado&municipio=Belo` — ambos opcionais; `municipio` é match parcial case-insensitive (`ilike %valor%`); `status_triagem` é match exato.

- [ ] **Step 1: Escrever o teste que falha**

Append em `apps/api/tests/test_licitacoes_triagem.py`:

```python
@pytest.mark.asyncio
async def test_list_filtra_por_status_triagem_e_municipio(
    api_client, db_session
) -> None:
    a = _mk_licitacao("trg-f1")  # BH / novo_captado
    b = _mk_licitacao("trg-f2")
    b.municipio_nome = "Uberlandia"
    b.status_triagem = "aprovado"
    db_session.add_all([a, b])
    await db_session.commit()

    r = await api_client.get("/api/v1/licitacoes?status_triagem=aprovado")
    assert [x["external_id"] for x in r.json()["data"]] == ["trg-f2"]

    r2 = await api_client.get("/api/v1/licitacoes?municipio=belo")
    assert [x["external_id"] for x in r2.json()["data"]] == ["trg-f1"]

    r3 = await api_client.get(
        "/api/v1/licitacoes?status_triagem=aprovado&municipio=uberl"
    )
    assert r3.json()["total"] == 1
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py -v -k filtra`
Expected: FAIL — filtros ignorados, listas voltam com 2 itens.

- [ ] **Step 3: Implementar**

Em `service.py`, na assinatura de `list_licitacoes`, adicionar os kwargs `status_triagem: str | None = None` e `municipio: str | None = None` (junto de `uf`/`modalidade`); no corpo, junto dos `where` existentes:

```python
    if status_triagem:
        stmt = stmt.where(Licitacao.status_triagem == status_triagem)
    if municipio:
        stmt = stmt.where(Licitacao.municipio_nome.ilike(f"%{municipio}%"))
```

(Aplicar nos DOIS statements — o de count e o de página — seguindo como `uf` é aplicado hoje; se a função monta os filtros uma vez e reusa, basta o ponto único.)

Em `router.py:list_endpoint`, adicionar os params e repassar:

```python
    status_triagem: str | None = Query(None, max_length=32),
    municipio: str | None = Query(None, max_length=128),
```

e na chamada `list_licitacoes(...)`: `status_triagem=status_triagem, municipio=municipio,`.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_licitacoes_triagem.py tests/test_licitacoes_router.py tests/test_licitacoes_service.py -v`
Expected: PASS.

- [ ] **Step 5: Suite completa + lint + commit**

```bash
cd apps/api && uv run --extra dev pytest tests/ -q && uv run --extra dev ruff check .
git add apps/api/app/modules/licitacoes/service.py apps/api/app/modules/licitacoes/router.py apps/api/tests/test_licitacoes_triagem.py
git commit -m "feat(captador): filtros status_triagem e municipio na listagem"
```

---

### Task 6: Tela de Captação (web)

**Files:**
- Create: `apps/web/src/app/(dashboard)/licitacoes/captacao/page.tsx`
- Create: `apps/web/src/app/(dashboard)/licitacoes/captacao/[id]/page.tsx`
- Modify: `apps/web/src/app/(dashboard)/licitacoes/page.tsx:92-108` (adicionar link no header)

**Interfaces:**
- Consumes: endpoints da Task 4 e filtros da Task 5, via `apiFetch` de `@/lib/api` (injeta JWT do cookie `mc_access_token`).
- Produces: rotas `/licitacoes/captacao` (lista + ações) e `/licitacoes/captacao/[id]` (detalhe + histórico + ações).

Padrões obrigatórios (copiados da página `/licitacoes` existente): Server Component async com `searchParams`, Server Actions (`"use server"`) + `revalidatePath`, tabela Tailwind `divide-y divide-slate-200`, `export const dynamic = "force-dynamic"`. Sem client components — o formulário de rejeição usa `<details>` nativo no lugar de modal JS.

- [ ] **Step 1: Criar a página de lista**

Criar `apps/web/src/app/(dashboard)/licitacoes/captacao/page.tsx`:

```tsx
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type LicitacaoRead = {
  id: number;
  external_id: string;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  valor_total_estimado: string | null;
  orgao_razao_social: string | null;
  orgao_cnpj: string | null;
  uf_sigla: string | null;
  municipio_nome: string | null;
  data_publicacao_pncp: string | null;
  status_triagem: string;
};

type ListResponse = {
  total: number;
  page: number;
  page_size: number;
  data: LicitacaoRead[];
};

export const dynamic = "force-dynamic";

const STATUS_LABELS: Record<string, string> = {
  novo_captado: "Novo Captado",
  em_analise: "Em Análise",
  aprovado: "Aprovado",
  rejeitado: "Rejeitado",
  processando_anexos: "Processando Anexos",
  completo: "Completo",
  sem_planilha: "Sem Planilha",
  erro_portal: "Erro de Portal",
  erro_sharepoint: "Erro SharePoint",
};

const STATUS_BADGE: Record<string, string> = {
  novo_captado: "bg-sky-100 text-sky-800",
  em_analise: "bg-amber-100 text-amber-800",
  aprovado: "bg-emerald-100 text-emerald-800",
  rejeitado: "bg-slate-200 text-slate-600",
  processando_anexos: "bg-indigo-100 text-indigo-800",
  completo: "bg-emerald-100 text-emerald-800",
  sem_planilha: "bg-red-100 text-red-800",
  erro_portal: "bg-red-100 text-red-800",
  erro_sharepoint: "bg-red-100 text-red-800",
};

async function aprovarAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/triagem/aprovar`, {
      method: "POST",
      body: JSON.stringify({ observacao: null }),
    });
  } catch (err) {
    console.error("[captacao] aprovar falhou", err);
  }
  revalidatePath("/licitacoes/captacao");
}

async function rejeitarAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  const observacao = String(formData.get("observacao") ?? "").trim();
  if (!id || observacao.length < 5) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/triagem/rejeitar`, {
      method: "POST",
      body: JSON.stringify({ observacao }),
    });
  } catch (err) {
    console.error("[captacao] rejeitar falhou", err);
  }
  revalidatePath("/licitacoes/captacao");
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

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE[status] ?? "bg-slate-100 text-slate-700"}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

export default async function CaptacaoPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const params = new URLSearchParams();
  const uf = typeof searchParams.uf === "string" ? searchParams.uf : "";
  const municipio =
    typeof searchParams.municipio === "string" ? searchParams.municipio : "";
  const status =
    typeof searchParams.status === "string" ? searchParams.status : "";
  const modalidade =
    typeof searchParams.modalidade === "string" ? searchParams.modalidade : "";
  const search = typeof searchParams.search === "string" ? searchParams.search : "";
  const page = typeof searchParams.page === "string" ? searchParams.page : "1";
  if (uf) params.set("uf", uf);
  if (municipio) params.set("municipio", municipio);
  if (status) params.set("status_triagem", status);
  if (modalidade) params.set("modalidade", modalidade);
  if (search) params.set("search", search);
  params.set("page", page);
  params.set("page_size", "20");

  let resp: ListResponse | null = null;
  try {
    resp = await apiFetch<ListResponse>(`/api/v1/licitacoes?${params.toString()}`);
  } catch {
    resp = null;
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Tela de Captação</h1>
          <p className="mt-1 text-sm text-slate-500">
            Triagem de editais captados: aprovar dispara pasta + anexos +
            planilha orçamentária; rejeitar exige motivo.
          </p>
        </div>
        <Link
          href="/licitacoes"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Licitações
        </Link>
      </header>

      <form className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4">
        <label className="flex flex-col text-xs font-medium text-slate-600">
          UF
          <input
            name="uf"
            defaultValue={uf}
            maxLength={2}
            placeholder="ex: MG"
            className="mt-1 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm uppercase"
          />
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Município (contém)
          <input
            name="municipio"
            defaultValue={municipio}
            placeholder="ex: Belo Horizonte"
            className="mt-1 w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Status
          <select
            name="status"
            defaultValue={status}
            className="mt-1 w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Todos</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Modalidade (contém)
          <input
            name="modalidade"
            defaultValue={modalidade}
            placeholder="ex: Pregão"
            className="mt-1 w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-1 flex-col text-xs font-medium text-slate-600">
          Busca no objeto
          <input
            name="search"
            defaultValue={search}
            placeholder="ex: pavimentação"
            maxLength={200}
            className="mt-1 w-full min-w-56 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
        >
          Filtrar
        </button>
      </form>

      {resp === null ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          Não foi possível conectar à API (<code>NEXT_PUBLIC_API_BASE_URL</code>).
        </div>
      ) : resp.data.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Nenhuma licitação para os filtros escolhidos.
        </div>
      ) : (
        <>
          <p className="text-xs text-slate-500">
            {resp.total.toLocaleString("pt-BR")} licitações · página {resp.page}
          </p>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Publicação</th>
                  <th className="px-4 py-3">UF / Município</th>
                  <th className="px-4 py-3">Órgão</th>
                  <th className="px-4 py-3">Objeto</th>
                  <th className="px-4 py-3 text-right">Valor estimado</th>
                  <th className="px-4 py-3">Triagem</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {resp.data.map((lic) => {
                  const decidivel =
                    lic.status_triagem === "novo_captado" ||
                    lic.status_triagem === "em_analise";
                  return (
                    <tr key={lic.id} className="align-top hover:bg-slate-50">
                      <td className="px-4 py-3">
                        <StatusBadge status={lic.status_triagem} />
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {formatDate(lic.data_publicacao_pncp)}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {lic.uf_sigla ?? "—"}
                        {lic.municipio_nome ? ` · ${lic.municipio_nome}` : ""}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {lic.orgao_razao_social ?? lic.orgao_cnpj ?? "—"}
                      </td>
                      <td
                        className="max-w-md truncate px-4 py-3 text-slate-700"
                        title={lic.objeto_compra ?? ""}
                      >
                        {lic.objeto_compra ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-slate-700">
                        {formatCurrency(lic.valor_total_estimado)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1">
                          {decidivel ? (
                            <>
                              <form action={aprovarAction}>
                                <input
                                  type="hidden"
                                  name="licitacao_id"
                                  value={lic.id}
                                />
                                <button
                                  type="submit"
                                  className="rounded-md bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-500"
                                >
                                  Aprovar
                                </button>
                              </form>
                              <details>
                                <summary className="cursor-pointer text-xs text-red-700 hover:underline">
                                  Rejeitar…
                                </summary>
                                <form
                                  action={rejeitarAction}
                                  className="mt-1 flex flex-col gap-1"
                                >
                                  <input
                                    type="hidden"
                                    name="licitacao_id"
                                    value={lic.id}
                                  />
                                  <textarea
                                    name="observacao"
                                    required
                                    minLength={5}
                                    rows={2}
                                    placeholder="Motivo (obrigatório)"
                                    className="w-48 rounded-md border border-slate-300 px-2 py-1 text-xs"
                                  />
                                  <button
                                    type="submit"
                                    className="self-start rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-500"
                                  >
                                    Confirmar rejeição
                                  </button>
                                </form>
                              </details>
                            </>
                          ) : null}
                          <Link
                            href={`/licitacoes/captacao/${lic.id}`}
                            className="text-xs text-slate-500 hover:underline"
                          >
                            histórico →
                          </Link>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Criar a página de detalhe/histórico**

Criar `apps/web/src/app/(dashboard)/licitacoes/captacao/[id]/page.tsx`:

```tsx
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type LicitacaoRead = {
  id: number;
  external_id: string;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  orgao_razao_social: string | null;
  uf_sigla: string | null;
  municipio_nome: string | null;
  status_triagem: string;
};

type DecisaoRead = {
  id: number;
  decisao: string;
  observacao: string | null;
  usuario_email: string;
  created_at: string;
};

export const dynamic = "force-dynamic";

async function observacaoAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  const observacao = String(formData.get("observacao") ?? "").trim();
  if (!id || !observacao) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/triagem/observacao`, {
      method: "POST",
      body: JSON.stringify({ observacao }),
    });
  } catch (err) {
    console.error("[captacao] observacao falhou", err);
  }
  revalidatePath(`/licitacoes/captacao/${id}`);
}

const DECISAO_LABELS: Record<string, string> = {
  aprovado: "Aprovado",
  rejeitado: "Rejeitado",
  observacao: "Observação",
};

export default async function CaptacaoDetalhePage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;

  let lic: LicitacaoRead | null = null;
  let decisoes: DecisaoRead[] = [];
  try {
    lic = await apiFetch<LicitacaoRead>(`/api/v1/licitacoes/${id}`);
    decisoes = await apiFetch<DecisaoRead[]>(`/api/v1/licitacoes/${id}/triagem`);
  } catch {
    lic = null;
  }

  if (!lic) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        Licitação não encontrada (ou API fora do ar).{" "}
        <Link href="/licitacoes/captacao" className="underline">
          Voltar
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <Link
          href="/licitacoes/captacao"
          className="text-sm text-slate-500 hover:underline"
        >
          ← Tela de Captação
        </Link>
        <h1 className="mt-2 text-2xl font-bold">
          Triagem — {lic.external_id}
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          {lic.objeto_compra ?? "Sem objeto"} · {lic.orgao_razao_social ?? "—"} ·{" "}
          {lic.uf_sigla ?? "—"}
          {lic.municipio_nome ? ` / ${lic.municipio_nome}` : ""} · status:{" "}
          <strong>{lic.status_triagem}</strong>
        </p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-700">
          Registrar observação
        </h2>
        <form action={observacaoAction} className="mt-2 flex items-start gap-2">
          <input type="hidden" name="licitacao_id" value={lic.id} />
          <textarea
            name="observacao"
            required
            rows={2}
            placeholder="Análise preliminar, restrições, pendências…"
            className="w-full max-w-xl rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            Salvar
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700">
          Histórico de decisões
        </h2>
        {decisoes.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">
            Nenhuma decisão registrada ainda.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {decisoes.map((d) => (
              <li key={d.id} className="px-4 py-3 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">
                    {DECISAO_LABELS[d.decisao] ?? d.decisao}
                  </span>
                  <span className="text-xs text-slate-500">
                    {d.usuario_email} ·{" "}
                    {new Date(d.created_at).toLocaleString("pt-BR")}
                  </span>
                </div>
                {d.observacao ? (
                  <p className="mt-1 text-slate-600">{d.observacao}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Linkar no header da página de licitações**

Em `apps/web/src/app/(dashboard)/licitacoes/page.tsx`, dentro do `<div className="flex items-center gap-2">` do header (linha ~92), adicionar ANTES do link de certidões:

```tsx
          <Link
            href="/licitacoes/captacao"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Tela de Captação →
          </Link>
```

- [ ] **Step 4: Verificar tipos e lint**

Run: `cd apps/web && npm run lint && npx tsc --noEmit`
Expected: sem erros. (Não há suite de testes unitários no web; o e2e Playwright existente cobre só o fluxo de parte diária.)

- [ ] **Step 5: Smoke manual (se a stack local estiver de pé)**

Com API + web rodando: abrir `/licitacoes/captacao`, aprovar uma licitação seed, ver badge mudar para "Aprovado"; rejeitar outra com motivo e conferir o histórico em `/licitacoes/captacao/<id>`.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/(dashboard)/licitacoes/captacao apps/web/src/app/(dashboard)/licitacoes/page.tsx
git commit -m "feat(captador): Tela de Captacao com aprovar/rejeitar/observacoes e historico"
```

---

## Self-review (feito na escrita do plano)

- **Cobertura da spec:** máquina de status (T1), persistência + trilha (T2), regras de negócio + audit + hook Squad 2 (T3), API (T4), filtros para a tela (T5), Tela de Captação com aprovar/rejeitar com motivo/observações/histórico e filtros UF/município/status/palavra-chave/modalidade (T6). Gap conhecido e proposital: modal JS de rejeição substituído por `<details>` (padrão server-component do repo, sem client components novos).
- **Contrato Squad 2:** nome exato da task, args, fila e flag documentados em Global Constraints e implementados em T3 com dispatch best-effort.
- **Tipos consistentes:** `DecisaoTriagem`/`DecisaoTriagemRead`, `status_triagem`, nomes de funções (`aprovar`/`rejeitar`/`registrar_observacao`/`listar_decisoes`) idênticos entre T3/T4/T6.
