# Squad 5 — Ciclo de Contratos (sem assinatura digital) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CRUD de contratos (cliente/fornecedor/locação) com upload do PDF, alertas de vencimento por email nas janelas 30/15/7/0 dias e página na UI — demanda #12 do roadmap, sem a perna de assinatura digital.

**Architecture:** Novo domínio dentro do módulo stub `apps/api/app/modules/financeiro_contratos/` (já montado em `/api/v1/financeiro`), espelhando 1:1 o padrão do D.6 (certidões): service assíncrono com audit_log, alertas idempotentes via `UniqueConstraint (contrato_id, janela)`, task Celery beat diária + endpoint de disparo manual (a demo Vercel não tem worker), storage plugável (`EditaisStorage` local/OneDrive) para o PDF. UI é uma página server-component Next.js idêntica em estilo à de certidões.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + Pydantic v2 + Celery + Resend + Next.js App Router (server actions).

## Global Constraints

- Rodar testes com `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`; a suíte inteira (`uv run --extra dev pytest`) tem que continuar 100% verde (565+ testes).
- `uv run --extra dev ruff check .` limpo antes de cada commit.
- Comentários e docstrings em português SEM acentos (padrão do repo: "nao", "modulo").
- Toda mutação (create/update/delete/upload) grava `AuditLog` com `actor=current_user.email` (AGENTS.md / LGPD).
- Squads paralelas criam migrations: antes de rodar a migration desta squad, recalcular o head com `cd apps/api && uv run --extra dev alembic heads` e ajustar `down_revision`. Head vigente na data deste plano: `b8c9d0e1f2a3`.
- Assinatura digital está FORA do escopo (provider indefinido). Decisão registrada na Task 1: NÃO criar campos `assinatura_provider`/`assinatura_status` agora; quando o provider for escolhido, entra via migration aditiva simples (`ADD COLUMN ... NULLABLE`), sem alteração disruptiva.
- Integração EasyJur real está FORA do escopo (API não pública, depende de contato comercial). O vínculo é o campo texto livre `easyjur_ref`.

---

### Task 1: Models `Contrato` + `ContratoAlertaLog` + migration

**Files:**
- Modify: `apps/api/app/modules/financeiro_contratos/models.py` (hoje só tem `from __future__ import annotations`)
- Create: `apps/api/alembic/versions/aa11bb22cc33_contratos.py`
- Test: `apps/api/tests/test_financeiro_contratos.py` (novo)

**Interfaces:**
- Consumes: `app.core.db.Base`, `obras_obra.id` (FK nullable, módulo obras já existente)
- Produces: `Contrato` (tabela `contratos`) e `ContratoAlertaLog` (tabela `contratos_alertas_log`, `UniqueConstraint("contrato_id", "janela", name="uq_contrato_alerta_janela")`), constantes `TIPOS_CONTRATO`, `STATUS_CONTRATO` — usados por todas as tasks seguintes.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_financeiro_contratos.py
"""Tests da Squad 5 -- ciclo de contratos (demanda #12, sem assinatura)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO,
    TIPOS_CONTRATO,
    Contrato,
    ContratoAlertaLog,
)


@pytest.mark.asyncio
async def test_contrato_model_roundtrip(db_session: AsyncSession) -> None:
    row = Contrato(
        titulo="Locacao de escavadeira CAT 320",
        contraparte_nome="TratorMax Ltda",
        contraparte_documento="12345678000190",
        tipo="locacao",
        valor=1500000,  # em centavos
        data_inicio=date(2026, 1, 1),
        data_fim=date(2026, 12, 31),
        status="vigente",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.id is not None
    assert row.obra_id is None
    assert row.arquivo_path is None
    assert row.easyjur_ref is None
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_contrato_alerta_log_unique_janela(db_session: AsyncSession) -> None:
    contrato = Contrato(
        titulo="X",
        contraparte_nome="Y",
        tipo="fornecedor",
        data_inicio=date(2026, 1, 1),
        status="vigente",
    )
    db_session.add(contrato)
    await db_session.commit()
    db_session.add(
        ContratoAlertaLog(
            contrato_id=contrato.id, janela="30d", recipients=["a@b.com"]
        )
    )
    await db_session.commit()
    db_session.add(
        ContratoAlertaLog(
            contrato_id=contrato.id, janela="30d", recipients=["a@b.com"]
        )
    )
    with pytest.raises(Exception):  # IntegrityError (sqlite) -- uq_contrato_alerta_janela
        await db_session.commit()
    await db_session.rollback()
    count = len(
        (await db_session.execute(select(ContratoAlertaLog.id))).scalars().all()
    )
    assert count == 1


def test_tipos_e_status_canonicos() -> None:
    assert {"cliente", "fornecedor", "locacao"} == set(dict(TIPOS_CONTRATO))
    assert {"rascunho", "vigente", "encerrado", "judicializado"} == set(
        dict(STATUS_CONTRATO)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: FAIL com `ImportError: cannot import name 'Contrato'`

- [ ] **Step 3: Write the models**

```python
# apps/api/app/modules/financeiro_contratos/models.py
"""Modelos do Modulo C -- ciclo de contratos (demanda #12).

Espelha o desenho do D.6 (certidoes): tabela principal + tabela de log
de alertas com UniqueConstraint (contrato_id, janela) garantindo
idempotencia do cron diario.

Decisao registrada (2026-08-04): assinatura digital fica fora ate o
provider ser definido. Quando entrar, sao 2 colunas nullable novas
(`assinatura_provider`, `assinatura_status`) -- migration aditiva,
nenhum campo atual muda. NAO criar as colunas agora (YAGNI).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Tipos e status canonicos. String livre no banco (mesmo racional das
# certidoes: adicionar valor novo sem migration); UI e validacao usam
# estas listas.
TIPOS_CONTRATO: tuple[tuple[str, str], ...] = (
    ("cliente", "Contrato com cliente"),
    ("fornecedor", "Contrato com fornecedor"),
    ("locacao", "Locacao de equipamento"),
)
TIPOS_CONTRATO_VALIDOS: frozenset[str] = frozenset(t for t, _ in TIPOS_CONTRATO)

STATUS_CONTRATO: tuple[tuple[str, str], ...] = (
    ("rascunho", "Rascunho"),
    ("vigente", "Vigente"),
    ("encerrado", "Encerrado"),
    ("judicializado", "Judicializado"),
)
STATUS_CONTRATO_VALIDOS: frozenset[str] = frozenset(s for s, _ in STATUS_CONTRATO)


class Contrato(Base):
    """Um contrato da Primor (cliente, fornecedor ou locacao).

    `valor` em centavos (int) -- evita float e casa com o padrao de
    valores monetarios do restante do repo. `data_fim` nullable: contrato
    por prazo indeterminado nao gera alerta de vencimento (mesmo
    tratamento de certidao sem validade no D.6).

    `easyjur_ref` e referencia textual livre (numero do processo /
    codigo interno EasyJur) enquanto a integracao real nao existe.
    """

    __tablename__ = "contratos"

    id: Mapped[int] = mapped_column(primary_key=True)
    titulo: Mapped[str] = mapped_column(String(255))
    contraparte_nome: Mapped[str] = mapped_column(String(255), index=True)
    contraparte_documento: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    tipo: Mapped[str] = mapped_column(String(32), index=True)
    obra_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("obras_obra.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    valor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_inicio: Mapped[date] = mapped_column(Date)
    data_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="rascunho", index=True)
    arquivo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    easyjur_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_contratos_status_data_fim", "status", "data_fim"),
    )


class ContratoAlertaLog(Base):
    """Log de alertas de vencimento enviados por janela (30d/15d/7d/0d).

    Mesmo contrato de idempotencia do `CertidaoAlertaLog`: o cron diario
    nunca reenvia a mesma janela do mesmo contrato -- UniqueConstraint
    (contrato_id, janela) impoe isso no banco. Entradas `failed` sao
    atualizadas in-place no retry.
    """

    __tablename__ = "contratos_alertas_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    contrato_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contratos.id", ondelete="CASCADE"),
        index=True,
    )
    janela: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    recipients: Mapped[list[str]] = mapped_column(JSON)
    resend_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="sent")
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint("contrato_id", "janela", name="uq_contrato_alerta_janela"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Write the migration**

Antes: `cd apps/api && uv run --extra dev alembic heads` — se o head não for `b8c9d0e1f2a3` (squad paralela mergeou antes), usar o head atual em `down_revision`.

```python
# apps/api/alembic/versions/aa11bb22cc33_contratos.py
"""contratos

Tabelas da Squad 5 (demanda #12): `contratos` (ciclo de contratos sem
assinatura digital) e `contratos_alertas_log` (idempotencia dos alertas
de vencimento, janelas 30/15/7/0 dias -- mesmo desenho do D.6).

Revision ID: aa11bb22cc33
Revises: b8c9d0e1f2a3
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "aa11bb22cc33"
down_revision = "b8c9d0e1f2a3"  # AJUSTAR se `alembic heads` mostrar outro head
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contratos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("titulo", sa.String(length=255), nullable=False),
        sa.Column("contraparte_nome", sa.String(length=255), nullable=False, index=True),
        sa.Column("contraparte_documento", sa.String(length=32), nullable=True, index=True),
        sa.Column("tipo", sa.String(length=32), nullable=False, index=True),
        sa.Column(
            "obra_id",
            sa.Integer(),
            sa.ForeignKey("obras_obra.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("valor", sa.Integer(), nullable=True),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("data_fim", sa.Date(), nullable=True, index=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="rascunho", index=True
        ),
        sa.Column("arquivo_path", sa.String(length=1024), nullable=True),
        sa.Column("easyjur_ref", sa.String(length=128), nullable=True),
        sa.Column("observacoes", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_contratos_status_data_fim", "contratos", ["status", "data_fim"]
    )
    op.create_table(
        "contratos_alertas_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contrato_id",
            sa.Integer(),
            sa.ForeignKey("contratos.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("janela", sa.String(length=32), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("resend_message_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="sent"),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.UniqueConstraint("contrato_id", "janela", name="uq_contrato_alerta_janela"),
    )


def downgrade() -> None:
    op.drop_table("contratos_alertas_log")
    op.drop_index("ix_contratos_status_data_fim", table_name="contratos")
    op.drop_table("contratos")
```

- [ ] **Step 6: Verify migration up/down against a scratch SQLite**

Run: `cd apps/api && DATABASE_URL="sqlite+aiosqlite:////tmp/contratos_mig_test.db" uv run --extra dev --with greenlet alembic upgrade head && DATABASE_URL="sqlite+aiosqlite:////tmp/contratos_mig_test.db" uv run --extra dev --with greenlet alembic downgrade -1 && rm -f /tmp/contratos_mig_test.db`
Expected: upgrade e downgrade sem erro. (Se o env de alembic do repo exigir Postgres, validar contra o Postgres do docker-compose local em vez de SQLite e anotar no PR.)

- [ ] **Step 7: Ruff + full suite + commit**

Run: `cd apps/api && uv run --extra dev ruff check . && uv run --extra dev pytest -q`
Expected: limpo, tudo verde.

```bash
git add apps/api/app/modules/financeiro_contratos/models.py apps/api/alembic/versions/aa11bb22cc33_contratos.py apps/api/tests/test_financeiro_contratos.py
git commit -m "feat(contratos): models Contrato + ContratoAlertaLog + migration (Squad 5, demanda #12)"
```

---

### Task 2: Service — CRUD com audit + status de vencimento

**Files:**
- Modify: `apps/api/app/modules/financeiro_contratos/service.py` (hoje stub)
- Test: `apps/api/tests/test_financeiro_contratos.py` (append)

**Interfaces:**
- Consumes: `Contrato`, `TIPOS_CONTRATO_VALIDOS`, `STATUS_CONTRATO_VALIDOS` (Task 1); `compute_status` e `janela_for_certidao` de `app.modules.licitacoes.certidoes` (helpers genéricos sobre uma data de validade — reuso DRY deliberado); `AuditLog` de `app.audit.models`; `SYSTEM` de `app.audit.actors`.
- Produces: `list_contratos(db, *, status=None, tipo=None, obra_id=None, vence_em_dias=None, today=None) -> list[Contrato]`, `get_contrato(db, contrato_id) -> Contrato | None`, `create_contrato(db, *, titulo, contraparte_nome, tipo, data_inicio, contraparte_documento=None, obra_id=None, valor=None, data_fim=None, status="rascunho", easyjur_ref=None, observacoes=None, actor=SYSTEM) -> Contrato`, `update_contrato(db, contrato_id, *, actor=SYSTEM, **fields) -> Contrato | None`, `delete_contrato(db, contrato_id, *, storage=None, actor=SYSTEM) -> bool`, `compute_vencimento_status(data_fim, *, today=None) -> str` (valores: `vigente | vencendo | vencido | sem_validade` — `sem_validade` = prazo indeterminado, a UI rotula "Sem prazo").

- [ ] **Step 1: Write the failing tests (append ao arquivo de teste)**

```python
# append em apps/api/tests/test_financeiro_contratos.py
from datetime import timedelta

from app.modules.financeiro_contratos.service import (
    compute_vencimento_status,
    create_contrato,
    delete_contrato,
    get_contrato,
    list_contratos,
    update_contrato,
)


def test_compute_vencimento_status_buckets() -> None:
    today = date(2026, 8, 4)
    assert compute_vencimento_status(date(2026, 12, 1), today=today) == "vigente"
    assert compute_vencimento_status(date(2026, 8, 20), today=today) == "vencendo"
    assert compute_vencimento_status(date(2026, 8, 1), today=today) == "vencido"
    assert compute_vencimento_status(None, today=today) == "sem_validade"


@pytest.mark.asyncio
async def test_create_e_get_contrato_com_audit(db_session: AsyncSession) -> None:
    row = await create_contrato(
        db_session,
        titulo="Obra BR-040 lote 2",
        contraparte_nome="DER-MG",
        tipo="cliente",
        data_inicio=date(2026, 1, 10),
        data_fim=date(2027, 1, 10),
        valor=250000000,
        status="vigente",
        actor="teste@primor.com",
    )
    assert row.id is not None
    fetched = await get_contrato(db_session, row.id)
    assert fetched is not None and fetched.titulo == "Obra BR-040 lote 2"
    # audit_log gravado com actor real
    from app.audit.models import AuditLog

    logs = (
        (await db_session.execute(select(AuditLog).where(
            AuditLog.resource == "financeiro.contrato"
        ))).scalars().all()
    )
    assert any(
        log.action == "create" and log.actor == "teste@primor.com" for log in logs
    )


@pytest.mark.asyncio
async def test_create_contrato_tipo_invalido(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await create_contrato(
            db_session,
            titulo="X",
            contraparte_nome="Y",
            tipo="permuta",  # nao esta em TIPOS_CONTRATO_VALIDOS
            data_inicio=date(2026, 1, 1),
        )


@pytest.mark.asyncio
async def test_list_contratos_filtros(db_session: AsyncSession) -> None:
    today = date(2026, 8, 4)
    await create_contrato(
        db_session, titulo="A", contraparte_nome="F1", tipo="fornecedor",
        data_inicio=today, data_fim=today + timedelta(days=10), status="vigente",
    )
    await create_contrato(
        db_session, titulo="B", contraparte_nome="F2", tipo="locacao",
        data_inicio=today, data_fim=today + timedelta(days=90), status="vigente",
    )
    await create_contrato(
        db_session, titulo="C", contraparte_nome="F3", tipo="cliente",
        data_inicio=today, status="encerrado",
    )
    assert len(await list_contratos(db_session)) == 3
    assert [c.titulo for c in await list_contratos(db_session, tipo="locacao")] == ["B"]
    assert [c.titulo for c in await list_contratos(db_session, status="encerrado")] == ["C"]
    # vence_em_dias=30: somente A (10 dias); B vence em 90; C sem data_fim
    vencendo = await list_contratos(db_session, vence_em_dias=30, today=today)
    assert [c.titulo for c in vencendo] == ["A"]


@pytest.mark.asyncio
async def test_update_e_delete_contrato(db_session: AsyncSession) -> None:
    row = await create_contrato(
        db_session, titulo="Antigo", contraparte_nome="Z", tipo="cliente",
        data_inicio=date(2026, 1, 1),
    )
    updated = await update_contrato(
        db_session, row.id, titulo="Novo", status="judicializado",
        easyjur_ref="EJ-2026-0042", actor="teste@primor.com",
    )
    assert updated is not None
    assert updated.titulo == "Novo"
    assert updated.easyjur_ref == "EJ-2026-0042"
    assert await update_contrato(db_session, 99999, titulo="x") is None
    assert await delete_contrato(db_session, row.id) is True
    assert await get_contrato(db_session, row.id) is None
    assert await delete_contrato(db_session, 99999) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: FAIL com `ImportError: cannot import name 'compute_vencimento_status'`

- [ ] **Step 3: Write the service**

```python
# apps/api/app/modules/financeiro_contratos/service.py  (substitui o stub)
"""Service do ciclo de contratos (Squad 5, demanda #12).

Mesmo desenho do D.6 (licitacoes/certidoes.py): funcoes async puras
sobre AsyncSession, audit_log em toda mutacao, helpers de status
computado reaproveitados do proprio D.6 (a semantica "data limite +
janelas 30/15/7/0" e identica -- mudou so o substantivo).
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.modules.licitacoes.certidoes import compute_status
from app.modules.licitacoes.storage import EditaisStorage
from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO_VALIDOS,
    TIPOS_CONTRATO_VALIDOS,
    Contrato,
)

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "financeiro.contrato"


# Reuso deliberado: as regras de bucket (vigente/vencendo/vencido/
# sem_validade, threshold 30d) sao as mesmas das certidoes. Para
# contratos, `sem_validade` significa prazo indeterminado (UI rotula
# "Sem prazo").
compute_vencimento_status = compute_status


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )
    await db.commit()


async def list_contratos(
    db: AsyncSession,
    *,
    status: str | None = None,
    tipo: str | None = None,
    obra_id: int | None = None,
    vence_em_dias: int | None = None,
    today: _date | None = None,
) -> list[Contrato]:
    stmt = select(Contrato).order_by(Contrato.data_fim.asc().nullslast())
    if status:
        stmt = stmt.where(Contrato.status == status)
    if tipo:
        stmt = stmt.where(Contrato.tipo == tipo)
    if obra_id is not None:
        stmt = stmt.where(Contrato.obra_id == obra_id)
    if vence_em_dias is not None:
        today = today or _date.today()
        stmt = stmt.where(
            Contrato.data_fim.isnot(None),
            Contrato.data_fim >= today,
            Contrato.data_fim <= today + timedelta(days=vence_em_dias),
        )
    return list((await db.execute(stmt)).scalars().all())


async def get_contrato(db: AsyncSession, contrato_id: int) -> Contrato | None:
    return await db.get(Contrato, contrato_id)


async def create_contrato(
    db: AsyncSession,
    *,
    titulo: str,
    contraparte_nome: str,
    tipo: str,
    data_inicio: _date,
    contraparte_documento: str | None = None,
    obra_id: int | None = None,
    valor: int | None = None,
    data_fim: _date | None = None,
    status: str = "rascunho",
    easyjur_ref: str | None = None,
    observacoes: str | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> Contrato:
    # Diferente das certidoes (string livre tolerada), tipo/status de
    # contrato sao um enum fechado -- valor fora da lista e erro de
    # programacao ou payload malicioso, nao um "custom" legitimo.
    if tipo not in TIPOS_CONTRATO_VALIDOS:
        raise ValueError(f"tipo de contrato invalido: {tipo}")
    if status not in STATUS_CONTRATO_VALIDOS:
        raise ValueError(f"status de contrato invalido: {status}")
    row = Contrato(
        titulo=titulo,
        contraparte_nome=contraparte_nome,
        contraparte_documento=contraparte_documento,
        tipo=tipo,
        obra_id=obra_id,
        valor=valor,
        data_inicio=data_inicio,
        data_fim=data_fim,
        status=status,
        easyjur_ref=easyjur_ref,
        observacoes=observacoes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _record_audit(
        db,
        action="create",
        resource_id=row.id,
        actor=actor,
        metadata={
            "titulo": row.titulo,
            "tipo": row.tipo,
            "contraparte_nome": row.contraparte_nome,
            "data_fim": row.data_fim,
        },
    )
    return row


async def update_contrato(
    db: AsyncSession,
    contrato_id: int,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
    **fields: object,
) -> Contrato | None:
    row = await db.get(Contrato, contrato_id)
    if row is None:
        return None
    if "tipo" in fields and fields["tipo"] not in TIPOS_CONTRATO_VALIDOS:
        raise ValueError(f"tipo de contrato invalido: {fields['tipo']}")
    if "status" in fields and fields["status"] not in STATUS_CONTRATO_VALIDOS:
        raise ValueError(f"status de contrato invalido: {fields['status']}")
    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if hasattr(row, key):
            old = getattr(row, key)
            if old != value:
                changed[key] = {"from": old, "to": value}
            setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    if changed:
        await _record_audit(
            db,
            action="update",
            resource_id=row.id,
            actor=actor,
            metadata={"changed": changed},
        )
    return row


async def delete_contrato(
    db: AsyncSession,
    contrato_id: int,
    *,
    storage: EditaisStorage | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> bool:
    """Remove o contrato e, se `storage` foi passado, o PDF anexo.

    Best-effort no storage (mesmo racional do delete_certidao): a linha
    do DB ja foi removida; falha na limpeza do arquivo apenas loga.
    """
    row = await db.get(Contrato, contrato_id)
    if row is None:
        return False
    snapshot = {
        "titulo": row.titulo,
        "tipo": row.tipo,
        "contraparte_nome": row.contraparte_nome,
    }
    arquivo_path = row.arquivo_path
    await db.delete(row)
    await db.commit()
    if storage is not None and arquivo_path:
        try:
            await storage.delete(arquivo_path)
        except Exception:  # noqa: BLE001
            logger.warning(
                "falha ao remover PDF do contrato em %s (DB ja apagou)",
                arquivo_path,
            )
    await _record_audit(
        db, action="delete", resource_id=contrato_id, actor=actor, metadata=snapshot
    )
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: PASS

- [ ] **Step 5: Ruff + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/financeiro_contratos/service.py apps/api/tests/test_financeiro_contratos.py
git commit -m "feat(contratos): service CRUD com audit + status de vencimento (reuso D.6)"
```

---

### Task 3: Schemas + router CRUD

**Files:**
- Modify: `apps/api/app/modules/financeiro_contratos/schemas.py`
- Modify: `apps/api/app/modules/financeiro_contratos/router.py`
- Test: `apps/api/tests/test_financeiro_contratos.py` (append)

**Interfaces:**
- Consumes: service da Task 2; `get_db`, `get_current_user`; fixtures `api_client` + `auth_headers` do conftest.
- Produces: rotas sob `/api/v1/financeiro/contratos` (router já montado em `main.py` com prefixo `/api/v1/financeiro` — nada a mudar lá): `GET /contratos`, `POST /contratos` (201), `GET /contratos/{id}`, `PATCH /contratos/{id}`, `DELETE /contratos/{id}` (204). Schemas: `ContratoCreate`, `ContratoUpdate`, `ContratoRead` (com computados `vencimento_status: str | None` e `dias_para_vencer: int | None`). O `GET /status` existente passa a responder `implemented=True`.
- IMPORTANTE (ordem de rotas): como no D.6, rotas estáticas futuras devem vir ANTES de `/{contrato_id}` no arquivo.

- [ ] **Step 1: Write the failing tests (append)**

```python
# append em apps/api/tests/test_financeiro_contratos.py
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_crud_contratos_via_api(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # create
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Locacao retroescavadeira",
            "contraparte_nome": "TratorMax",
            "contraparte_documento": "12345678000190",
            "tipo": "locacao",
            "valor": 900000,
            "data_inicio": "2026-08-01",
            "data_fim": "2026-08-20",
            "status": "vigente",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    contrato_id = body["id"]
    assert body["vencimento_status"] in ("vencendo", "vigente")
    assert body["dias_para_vencer"] is not None

    # list + filtro
    resp = await api_client.get("/api/v1/financeiro/contratos?tipo=locacao")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # get
    resp = await api_client.get(f"/api/v1/financeiro/contratos/{contrato_id}")
    assert resp.status_code == 200
    assert resp.json()["titulo"] == "Locacao retroescavadeira"

    # patch
    resp = await api_client.patch(
        f"/api/v1/financeiro/contratos/{contrato_id}",
        json={"status": "judicializado", "easyjur_ref": "EJ-77"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["easyjur_ref"] == "EJ-77"

    # delete
    resp = await api_client.delete(
        f"/api/v1/financeiro/contratos/{contrato_id}", headers=auth_headers
    )
    assert resp.status_code == 204
    resp = await api_client.get(f"/api/v1/financeiro/contratos/{contrato_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mutacoes_exigem_auth(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "X", "contraparte_nome": "Y", "tipo": "cliente",
            "data_inicio": "2026-01-01",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tipo_invalido_da_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "X", "contraparte_nome": "Y", "tipo": "permuta",
            "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_status_endpoint_implemented(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/financeiro/status")
    assert resp.status_code == 200
    assert resp.json()["implemented"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v -k "api or auth or invalido or implemented"`
Expected: FAIL — `POST /api/v1/financeiro/contratos` devolve 404 (rota não existe) e `implemented` é `False`.

- [ ] **Step 3: Write schemas**

```python
# apps/api/app/modules/financeiro_contratos/schemas.py  (substitui o conteudo atual)
"""Schemas do ciclo de contratos (Squad 5)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ContratoCreate(BaseModel):
    titulo: str = Field(min_length=1, max_length=255)
    contraparte_nome: str = Field(min_length=1, max_length=255)
    contraparte_documento: str | None = Field(default=None, max_length=32)
    tipo: str = Field(min_length=1, max_length=32)
    obra_id: int | None = None
    valor: int | None = Field(default=None, ge=0, description="Valor em centavos")
    data_inicio: date
    data_fim: date | None = None
    status: str = Field(default="rascunho", max_length=32)
    easyjur_ref: str | None = Field(default=None, max_length=128)
    observacoes: str | None = Field(default=None, max_length=2048)


class ContratoUpdate(BaseModel):
    titulo: str | None = Field(default=None, min_length=1, max_length=255)
    contraparte_nome: str | None = Field(default=None, min_length=1, max_length=255)
    contraparte_documento: str | None = Field(default=None, max_length=32)
    tipo: str | None = Field(default=None, max_length=32)
    obra_id: int | None = None
    valor: int | None = Field(default=None, ge=0)
    data_inicio: date | None = None
    data_fim: date | None = None
    status: str | None = Field(default=None, max_length=32)
    easyjur_ref: str | None = Field(default=None, max_length=128)
    observacoes: str | None = Field(default=None, max_length=2048)


class ContratoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    contraparte_nome: str
    contraparte_documento: str | None
    tipo: str
    obra_id: int | None
    valor: int | None
    data_inicio: date
    data_fim: date | None
    status: str
    arquivo_path: str | None
    easyjur_ref: str | None
    observacoes: str | None
    created_at: datetime
    updated_at: datetime

    # Computados (router preenche via compute_vencimento_status):
    # vigente | vencendo | vencido | sem_validade (= prazo indeterminado)
    vencimento_status: str | None = None
    dias_para_vencer: int | None = None


class ContratoAlertaResult(BaseModel):
    contrato_id: int
    janela: str
    status: str
    recipients: list[str]
    resend_message_id: str | None = None
    error_message: str | None = None


class ContratoAlertaSummary(BaseModel):
    total_contratos: int
    sent: int
    skipped: int
    failed: int
    results: list[ContratoAlertaResult]


class ContratoAlertaDispatchPayload(BaseModel):
    """Payload do POST /contratos/dispatch-alerts (paridade com D.6)."""

    recipients: list[str] = Field(min_length=1)
```

- [ ] **Step 4: Write the router**

```python
# apps/api/app/modules/financeiro_contratos/router.py  (substitui o stub)
"""Modulo C - Financeiro e Contratos.

Rotas do ciclo de contratos (Squad 5, demanda #12) sob
`/api/v1/financeiro/contratos`. Assinatura digital e integracao
EasyJur real ficam fora (ver models.py).

Ordem de rotas: estaticas (`/contratos/dispatch-alerts`, Task 5) ANTES
de `/contratos/{contrato_id}` -- mesmo racional do D.6.
"""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.dp_sesmt.schemas import ModuleStatus
from app.modules.financeiro_contratos.schemas import (
    ContratoCreate,
    ContratoRead,
    ContratoUpdate,
)
from app.modules.financeiro_contratos.service import (
    compute_vencimento_status,
    create_contrato,
    delete_contrato,
    get_contrato,
    list_contratos,
    update_contrato,
)

router = APIRouter()


def _contrato_to_read(contrato, *, today: _date | None = None) -> ContratoRead:
    """Hidrata a view com os computados de vencimento (padrao D.6)."""
    today = today or _date.today()
    payload = ContratoRead.model_validate(contrato)
    payload.vencimento_status = compute_vencimento_status(
        contrato.data_fim, today=today
    )
    if contrato.data_fim is not None:
        payload.dias_para_vencer = (contrato.data_fim - today).days
    return payload


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="financeiro_contratos", implemented=True)


@router.get("/contratos", response_model=list[ContratoRead])
async def list_contratos_endpoint(
    status: str | None = Query(None, max_length=32),
    tipo: str | None = Query(None, max_length=32),
    obra_id: int | None = Query(None),
    vence_em_dias: int | None = Query(
        None, ge=0, le=365,
        description="Somente contratos com data_fim entre hoje e hoje+N dias",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[ContratoRead]:
    rows = await list_contratos(
        db, status=status, tipo=tipo, obra_id=obra_id, vence_em_dias=vence_em_dias
    )
    today = _date.today()
    return [_contrato_to_read(r, today=today) for r in rows]


@router.post("/contratos", response_model=ContratoRead, status_code=201)
async def create_contrato_endpoint(
    payload: ContratoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    try:
        row = await create_contrato(
            db,
            titulo=payload.titulo,
            contraparte_nome=payload.contraparte_nome,
            contraparte_documento=payload.contraparte_documento,
            tipo=payload.tipo,
            obra_id=payload.obra_id,
            valor=payload.valor,
            data_inicio=payload.data_inicio,
            data_fim=payload.data_fim,
            status=payload.status,
            easyjur_ref=payload.easyjur_ref,
            observacoes=payload.observacoes,
            actor=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _contrato_to_read(row)


@router.get("/contratos/{contrato_id}", response_model=ContratoRead)
async def get_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
) -> ContratoRead:
    row = await get_contrato(db, contrato_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.patch("/contratos/{contrato_id}", response_model=ContratoRead)
async def update_contrato_endpoint(
    contrato_id: int,
    payload: ContratoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    fields = payload.model_dump(exclude_unset=True)
    try:
        row = await update_contrato(
            db, contrato_id, actor=current_user.email, **fields
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.delete("/contratos/{contrato_id}", status_code=204)
async def delete_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_contrato(db, contrato_id, actor=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
```

Nota: o `delete` ainda não passa `storage=` — o dep de storage entra na Task 4, que atualiza este endpoint.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: PASS (todos)

- [ ] **Step 6: Ruff + full suite + commit**

```bash
cd apps/api && uv run --extra dev ruff check . && uv run --extra dev pytest -q
git add apps/api/app/modules/financeiro_contratos/schemas.py apps/api/app/modules/financeiro_contratos/router.py apps/api/tests/test_financeiro_contratos.py
git commit -m "feat(contratos): CRUD REST /financeiro/contratos com computados de vencimento"
```

---

### Task 4: Upload do PDF do contrato via storage

**Files:**
- Modify: `apps/api/app/modules/financeiro_contratos/router.py`
- Modify: `apps/api/app/core/config.py` (1 campo novo)
- Test: `apps/api/tests/test_financeiro_contratos.py` (append)

**Interfaces:**
- Consumes: protocolo `EditaisStorage` + `LocalStorage` (`app.modules.licitacoes.storage`), `OneDriveStorage`/`build_onedrive_client` (`app.integrations.onedrive.storage`) — copiar o desenho de `get_fiscal_storage` em `apps/api/app/modules/fiscal/router.py:55` (subpasta própria para não colidir namespace numérico).
- Produces: `get_contratos_storage()` (async generator dependency), `POST /contratos/{contrato_id}/arquivo` (multipart, campo `arquivo`) que salva e grava `arquivo_path`; `DELETE /contratos/{contrato_id}` passa a limpar o arquivo via storage. Config novo: `contratos_storage_subdir: str = "MotorCentral/contratos"`.

- [ ] **Step 1: Write the failing test (append)**

```python
# append em apps/api/tests/test_financeiro_contratos.py
@pytest.mark.asyncio
async def test_upload_arquivo_contrato(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forca LocalStorage numa raiz temporaria
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EDITAIS_STORAGE_PATH", str(tmp_path / "editais"))
    from app.core.config import get_settings

    get_settings.cache_clear()

    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Com anexo", "contraparte_nome": "Z", "tipo": "cliente",
            "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    contrato_id = resp.json()["id"]

    resp = await api_client.post(
        f"/api/v1/financeiro/contratos/{contrato_id}/arquivo",
        files={"arquivo": ("contrato.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["arquivo_path"] is not None
    assert "contratos" in body["arquivo_path"]

    # 404 para contrato inexistente
    resp = await api_client.post(
        "/api/v1/financeiro/contratos/99999/arquivo",
        files={"arquivo": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v -k upload`
Expected: FAIL com 404 (rota `/arquivo` não existe — atenção: o teste de contrato inexistente também dá 404; o assert do primeiro upload é o que falha)

- [ ] **Step 3: Add config field**

Em `apps/api/app/core/config.py`, logo abaixo de `fiscal_storage_subdir` (linha ~112):

```python
    # Subpasta do storage para PDFs de contrato (Squad 5). Mesmo backend
    # do storage de editais (`STORAGE_BACKEND=local|onedrive`), raiz
    # separada para nao colidir com o namespace numerico dos editais.
    contratos_storage_subdir: str = Field(default="MotorCentral/contratos")
```

- [ ] **Step 4: Add storage dep + upload endpoint**

No `router.py` de financeiro_contratos, adicionar imports e o dep (copiar o desenho de `get_fiscal_storage`):

```python
# imports adicionais no topo do router.py
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import File, UploadFile

from app.core.config import get_settings
from app.integrations.onedrive.client import build_onedrive_client
from app.integrations.onedrive.storage import OneDriveStorage
from app.modules.licitacoes.storage import EditaisStorage, LocalStorage
from app.modules.financeiro_contratos.service import set_arquivo_contrato


async def get_contratos_storage() -> AsyncIterator[EditaisStorage]:
    """Storage dedicado para PDFs de contrato.

    Mesmo backend do storage de editais, raiz `contratos_storage_subdir`
    separada. Async generator para garantir aclose() do client OneDrive
    no fim da request (padrao get_fiscal_storage).
    """
    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.contratos_storage_subdir,
        )
        try:
            yield OneDriveStorage(client)
        finally:
            await client.aclose()
        return
    base = Path(settings.editais_storage_path).parent
    yield LocalStorage(base / settings.contratos_storage_subdir)


@router.post("/contratos/{contrato_id}/arquivo", response_model=ContratoRead)
async def upload_arquivo_contrato_endpoint(
    contrato_id: int,
    arquivo: Annotated[UploadFile, File(description="PDF do contrato")],
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_contratos_storage),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    """Anexa/substitui o PDF do contrato. Substituicao nao apaga o
    arquivo antigo do storage (historico barato; limpeza so no delete
    do contrato)."""
    content = await arquivo.read()
    if not content:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    row = await set_arquivo_contrato(
        db,
        contrato_id,
        storage=storage,
        filename=arquivo.filename or "contrato.pdf",
        content=content,
        actor=current_user.email,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)
```

E no `service.py`, a função consumida acima:

```python
# append em apps/api/app/modules/financeiro_contratos/service.py
from collections.abc import AsyncIterator as _AsyncIterator


async def set_arquivo_contrato(
    db: AsyncSession,
    contrato_id: int,
    *,
    storage: EditaisStorage,
    filename: str,
    content: bytes,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> Contrato | None:
    row = await db.get(Contrato, contrato_id)
    if row is None:
        return None

    async def _chunks() -> _AsyncIterator[bytes]:
        yield content

    # `licitacao_id` no protocolo e so o bucket/subdir numerico -- aqui
    # usamos o id do contrato (raiz separada evita colisao com editais).
    storage_path, size = await storage.save(
        licitacao_id=row.id, filename=filename, content=_chunks()
    )
    row.arquivo_path = storage_path
    await db.commit()
    await db.refresh(row)
    await _record_audit(
        db,
        action="upload_arquivo",
        resource_id=row.id,
        actor=actor,
        metadata={"filename": filename, "size_bytes": size},
    )
    return row
```

Atualizar também o `delete_contrato_endpoint` (Task 3) para limpar o anexo:

```python
@router.delete("/contratos/{contrato_id}", status_code=204)
async def delete_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_contratos_storage),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_contrato(
        db, contrato_id, storage=storage, actor=current_user.email
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: PASS

- [ ] **Step 6: Ruff + full suite + commit**

```bash
cd apps/api && uv run --extra dev ruff check . && uv run --extra dev pytest -q
git add apps/api/app/modules/financeiro_contratos/ apps/api/app/core/config.py apps/api/tests/test_financeiro_contratos.py
git commit -m "feat(contratos): upload de PDF via storage plugavel (local/OneDrive)"
```

---

### Task 5: Alertas de vencimento (service + endpoint manual)

**Files:**
- Create: `apps/api/app/modules/financeiro_contratos/alerts.py`
- Modify: `apps/api/app/modules/financeiro_contratos/router.py` (endpoint dispatch — inserir ANTES das rotas `/contratos/{contrato_id}`)
- Test: `apps/api/tests/test_financeiro_contratos.py` (append)

**Interfaces:**
- Consumes: `janela_for_certidao` de `app.modules.licitacoes.certidoes` (helper genérico sobre data-limite), `ResendClient`/`ResendError` de `app.integrations.resend.client`, `ContratoAlertaLog` (Task 1), schemas de alerta (Task 3).
- Produces: `dispatch_contrato_alerts(db, resend, *, recipients, public_base_url=None, from_email=None, today=None) -> ContratoAlertaSummaryData` e `render_alerta_contrato_html(...) -> str`; endpoint `POST /contratos/dispatch-alerts`. Regra de negócio: SÓ contratos com status `vigente` ou `judicializado` e `data_fim` não nula entram no loop; demais contam como `skipped` (status de resultado `skipped_status` / `skipped_no_data_fim`).
- O corpo do dispatch replica o desenho de `dispatch_expiration_alerts` do D.6 (snapshot dataclass contra `MissingGreenlet`, commit per-item, retry in-place de logs `failed`). Repetição deliberada em vez de generalizar o loop do D.6: os campos do email e as regras de skip diferem, e abstrair 2 casos criaria acoplamento entre squads (regra do repo: generalizar só no 3º uso).

- [ ] **Step 1: Write the failing tests (append)**

```python
# append em apps/api/tests/test_financeiro_contratos.py
import httpx

from app.integrations.resend.client import ResendClient
from app.modules.financeiro_contratos.alerts import (
    dispatch_contrato_alerts,
    render_alerta_contrato_html,
)


def _fake_resend(sent: list[dict]) -> ResendClient:
    """ResendClient com transporte mockado (mesma tecnica do teste D.6)."""

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        sent.append(_json.loads(request.content))
        return httpx.Response(200, json={"id": f"msg_{len(sent)}"})

    client = ResendClient(api_key="test-key")
    client._client = httpx.AsyncClient(  # noqa: SLF001 -- mock de transporte
        transport=httpx.MockTransport(handler),
        base_url="https://api.resend.com",
        headers={"Authorization": "Bearer test-key"},
    )
    return client


def test_render_alerta_contrato_html_essentials() -> None:
    from app.modules.financeiro_contratos.models import Contrato

    c = Contrato(
        id=7,
        titulo="Locacao escavadeira",
        contraparte_nome="TratorMax",
        tipo="locacao",
        data_inicio=date(2026, 1, 1),
        data_fim=date(2026, 8, 15),
        status="vigente",
    )
    html = render_alerta_contrato_html(
        c, janela=15, public_base_url="https://motor.example", dias_restantes=11
    )
    assert "Locacao escavadeira" in html
    assert "TratorMax" in html
    assert "15/08/2026" in html
    assert "11 dia(s)" in html
    assert "https://motor.example/financeiro/contratos" in html


@pytest.mark.asyncio
async def test_dispatch_contrato_alerts_idempotente(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 8, 4)
    vigente = await create_contrato(
        db_session, titulo="Vence em 10d", contraparte_nome="A", tipo="fornecedor",
        data_inicio=today, data_fim=today + timedelta(days=10), status="vigente",
    )
    await create_contrato(  # encerrado -> skipped_status
        db_session, titulo="Encerrado", contraparte_nome="B", tipo="cliente",
        data_inicio=today, data_fim=today + timedelta(days=5), status="encerrado",
    )
    await create_contrato(  # sem data_fim -> skipped_no_data_fim
        db_session, titulo="Indeterminado", contraparte_nome="C", tipo="cliente",
        data_inicio=today, status="vigente",
    )

    sent: list[dict] = []
    resend = _fake_resend(sent)
    try:
        summary = await dispatch_contrato_alerts(
            db_session, resend, recipients=["fin@primor.com"], today=today,
            public_base_url="https://motor.example",
        )
    finally:
        await resend.aclose()
    assert summary.sent == 1
    assert summary.skipped == 2
    assert summary.failed == 0
    assert len(sent) == 1
    assert "Vence em 10d" in sent[0]["html"]

    # segunda rodada no mesmo dia: nada novo (janela 15d ja logada)
    sent2: list[dict] = []
    resend2 = _fake_resend(sent2)
    try:
        summary2 = await dispatch_contrato_alerts(
            db_session, resend2, recipients=["fin@primor.com"], today=today,
            public_base_url="https://motor.example",
        )
    finally:
        await resend2.aclose()
    assert summary2.sent == 0
    assert len(sent2) == 0
    # e o log existe para o contrato vigente
    logs = (
        (await db_session.execute(
            select(ContratoAlertaLog).where(
                ContratoAlertaLog.contrato_id == vigente.id
            )
        )).scalars().all()
    )
    assert len(logs) == 1 and logs[0].janela == "15d" and logs[0].status == "sent"
```

Nota para o implementador: antes de escrever `_fake_resend`, abrir `apps/api/tests/test_licitacoes_certidoes.py` e copiar a técnica de mock do Resend usada lá (se o helper de lá for diferente — ex. construtor aceita `transport=` — seguir o existente, ajustando este teste).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v -k alerta`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.modules.financeiro_contratos.alerts'`

- [ ] **Step 3: Write alerts.py**

```python
# apps/api/app/modules/financeiro_contratos/alerts.py
"""Alertas de vencimento de contratos (Squad 5).

Copia deliberada do desenho de `licitacoes/certidoes.py` (D.6):
janelas 30/15/7/0 dias, log idempotente por (contrato_id, janela),
commit per-item, retry in-place de envios failed. Diferencas:

- So contratos `vigente` ou `judicializado` alertam; `rascunho` e
  `encerrado` sao skipped.
- Contrato sem `data_fim` (prazo indeterminado) e skipped.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.resend.client import ResendClient, ResendError
from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO,
    Contrato,
    ContratoAlertaLog,
)
from app.modules.licitacoes.certidoes import janela_for_certidao

logger = logging.getLogger(__name__)

# Status que geram alerta. Judicializado continua alertando: vencimento
# de contrato em juizo e justamente o que o financeiro nao pode perder.
_STATUS_ALERTAVEIS = frozenset({"vigente", "judicializado"})


@dataclass(slots=True)
class AlertaResult:
    contrato_id: int
    janela: str
    status: str  # sent | skipped_status | skipped_no_data_fim | skipped_already_sent | failed
    recipients: list[str]
    resend_message_id: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class AlertaSummary:
    total_contratos: int
    sent: int
    skipped: int
    failed: int
    results: list[AlertaResult]


@dataclass(slots=True)
class _ContratoSnapshot:
    """Snapshot detached -- mesmo racional do `_CertidaoSnapshot` do D.6
    (rollback per-item expira ORM objects; lazy-refresh em AsyncSession
    estoura MissingGreenlet)."""

    id: int
    titulo: str
    contraparte_nome: str
    tipo: str
    status: str
    data_fim: _date | None


def render_alerta_contrato_html(
    contrato: Contrato | _ContratoSnapshot,
    *,
    janela: int,
    public_base_url: str,
    dias_restantes: int | None = None,
) -> str:
    """Email de alerta. `janela` da a urgencia/cor; `dias_restantes` e o
    numero real exibido (mesma distincao do D.6)."""
    status_label = dict(STATUS_CONTRATO).get(contrato.status, contrato.status)
    dias = dias_restantes if dias_restantes is not None else janela
    if janela == 0:
        urgencia = "VENCE hoje" if dias == 0 else f"Vence em {dias} dia(s)"
        cor = "#dc2626"
    elif janela <= 7:
        urgencia = f"Vence em {dias} dia(s)"
        cor = "#ea580c"
    elif janela <= 15:
        urgencia = f"Vence em {dias} dia(s)"
        cor = "#d97706"
    else:
        urgencia = f"Vence em {dias} dia(s)"
        cor = "#0284c7"
    data_fim_str = (
        contrato.data_fim.strftime("%d/%m/%Y") if contrato.data_fim else "-"
    )
    dashboard_url = f"{public_base_url.rstrip('/')}/financeiro/contratos"
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 640px; margin: 0 auto; color: #0f172a;">
      <h2 style="margin: 0 0 4px;">Contrato com vencimento proximo</h2>
      <p style="margin: 0 0 16px; color: {cor}; font-weight: 600; font-size: 14px;">
        {escape(urgencia)}
      </p>
      <table style="width: 100%; border-collapse: collapse; border: 1px solid #e5e7eb;">
        <tbody>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc; width: 30%;">Contrato</td>
            <td style="padding: 8px; font-size: 13px;">{escape(contrato.titulo)}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Contraparte</td>
            <td style="padding: 8px; font-size: 13px;">{escape(contrato.contraparte_nome)}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Status</td>
            <td style="padding: 8px; font-size: 13px;">{escape(status_label)}</td>
          </tr>
          <tr>
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Data fim</td>
            <td style="padding: 8px; font-size: 13px; color: {cor}; font-weight: 600;">{escape(data_fim_str)}</td>
          </tr>
        </tbody>
      </table>
      <p style="margin-top: 20px; font-size: 13px;">
        <a href="{escape(dashboard_url)}" style="color: #2563eb; text-decoration: none;">
          Abrir Motor Central -> Contratos ->
        </a>
      </p>
      <p style="margin-top: 24px; font-size: 11px; color: #94a3b8;">
        Alerta automatico de vencimento de contrato. Janelas: 30, 15, 7 e 0
        dias antes da data fim.
      </p>
    </div>
    """.strip()


async def _alerta_already_sent(
    db: AsyncSession, contrato_id: int, janela: str
) -> bool:
    stmt = (
        select(ContratoAlertaLog.id)
        .where(ContratoAlertaLog.contrato_id == contrato_id)
        .where(ContratoAlertaLog.janela == janela)
        .where(ContratoAlertaLog.status == "sent")
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _get_existing_log(
    db: AsyncSession, contrato_id: int, janela: str
) -> ContratoAlertaLog | None:
    stmt = (
        select(ContratoAlertaLog)
        .where(ContratoAlertaLog.contrato_id == contrato_id)
        .where(ContratoAlertaLog.janela == janela)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def dispatch_contrato_alerts(
    db: AsyncSession,
    resend: ResendClient,
    *,
    recipients: Sequence[str],
    public_base_url: str | None = None,
    from_email: str | None = None,
    today: _date | None = None,
) -> AlertaSummary:
    """Dispara alertas de vencimento de contratos (paridade com o
    dispatch_expiration_alerts do D.6 -- ver docstring do modulo)."""
    settings = get_settings()
    public_base_url = public_base_url or settings.public_base_url
    from_email = from_email or settings.resend_from_email
    today = today or _date.today()

    if not recipients:
        logger.warning("dispatch_contrato_alerts: lista de recipients vazia")
        return AlertaSummary(0, 0, 0, 0, [])

    stmt = select(
        Contrato.id,
        Contrato.titulo,
        Contrato.contraparte_nome,
        Contrato.tipo,
        Contrato.status,
        Contrato.data_fim,
    )
    rows = (await db.execute(stmt)).all()
    contratos = [
        _ContratoSnapshot(
            id=r.id,
            titulo=r.titulo,
            contraparte_nome=r.contraparte_nome,
            tipo=r.tipo,
            status=r.status,
            data_fim=r.data_fim,
        )
        for r in rows
    ]

    sent = 0
    skipped = 0
    failed = 0
    results: list[AlertaResult] = []

    for contrato in contratos:
        if contrato.status not in _STATUS_ALERTAVEIS:
            skipped += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela="none",
                    status="skipped_status",
                    recipients=list(recipients),
                )
            )
            continue
        janela = janela_for_certidao(contrato.data_fim, today=today)
        if janela is None:
            skipped += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela="none",
                    status="skipped_no_data_fim",
                    recipients=list(recipients),
                )
            )
            continue

        janela_str = f"{janela}d"
        if await _alerta_already_sent(db, contrato.id, janela_str):
            skipped += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    status="skipped_already_sent",
                    recipients=list(recipients),
                )
            )
            continue

        assert contrato.data_fim is not None  # janela filtrou None
        dias_restantes = (contrato.data_fim - today).days
        html = render_alerta_contrato_html(
            contrato,
            janela=janela,
            public_base_url=public_base_url,
            dias_restantes=dias_restantes,
        )
        data_fim_str = contrato.data_fim.strftime("%d/%m/%Y")
        if dias_restantes == 0:
            subject = (
                f"[Motor Central] Contrato VENCE hoje: {contrato.titulo} "
                f"({data_fim_str})"
            )
        else:
            subject = (
                f"[Motor Central] Contrato vence em {dias_restantes} dia(s): "
                f"{contrato.titulo} ({data_fim_str})"
            )

        existing_log = await _get_existing_log(db, contrato.id, janela_str)

        try:
            resp = await resend.send_email(
                to=list(recipients),
                subject=subject,
                html=html,
                from_=from_email,
            )
        except (ResendError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "alerta contrato=%s janela=%s falhou: %s",
                contrato.id,
                janela_str,
                exc,
                exc_info=False,
            )
            error_msg = str(exc)[:1024]
            if existing_log is None:
                db.add(
                    ContratoAlertaLog(
                        contrato_id=contrato.id,
                        janela=janela_str,
                        recipients=list(recipients),
                        status="failed",
                        error_message=error_msg,
                    )
                )
            else:
                existing_log.recipients = list(recipients)
                existing_log.status = "failed"
                existing_log.error_message = error_msg
            try:
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "falha ao gravar log de erro do alerta contrato=%s janela=%s",
                    contrato.id,
                    janela_str,
                )
                await db.rollback()
            failed += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message=error_msg,
                )
            )
            continue

        message_id = resp.get("id") if isinstance(resp, dict) else None
        if existing_log is None:
            db.add(
                ContratoAlertaLog(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    recipients=list(recipients),
                    resend_message_id=message_id,
                    status="sent",
                )
            )
        else:
            existing_log.recipients = list(recipients)
            existing_log.resend_message_id = message_id
            existing_log.status = "sent"
            existing_log.error_message = None
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "falha ao gravar log de sucesso do alerta contrato=%s janela=%s",
                contrato.id,
                janela_str,
            )
            await db.rollback()
            failed += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message="db_commit_failed",
                )
            )
            continue
        sent += 1
        results.append(
            AlertaResult(
                contrato_id=contrato.id,
                janela=janela_str,
                status="sent",
                recipients=list(recipients),
                resend_message_id=message_id,
            )
        )

    return AlertaSummary(
        total_contratos=len(contratos),
        sent=sent,
        skipped=skipped,
        failed=failed,
        results=results,
    )
```

- [ ] **Step 4: Add the dispatch endpoint (ANTES das rotas `/contratos/{contrato_id}` no router.py)**

```python
# imports adicionais no router.py
from app.integrations.resend.client import ResendClient
from app.modules.financeiro_contratos.alerts import dispatch_contrato_alerts
from app.modules.financeiro_contratos.schemas import (
    ContratoAlertaDispatchPayload,
    ContratoAlertaSummary,
)


@router.post("/contratos/dispatch-alerts", response_model=ContratoAlertaSummary)
async def dispatch_contrato_alerts_endpoint(
    payload: ContratoAlertaDispatchPayload,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ContratoAlertaSummary:
    """Disparo manual dos alertas (a demo Vercel nao tem worker/beat).
    Requires RESEND_API_KEY; 503 se ausente -- paridade com o D.6."""
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(
            status_code=503,
            detail="RESEND_API_KEY nao configurada; configure em settings.",
        )
    resend = ResendClient(api_key=settings.resend_api_key)
    try:
        summary = await dispatch_contrato_alerts(
            db, resend, recipients=[str(r) for r in payload.recipients]
        )
    finally:
        await resend.aclose()
    return ContratoAlertaSummary(
        total_contratos=summary.total_contratos,
        sent=summary.sent,
        skipped=summary.skipped,
        failed=summary.failed,
        results=[
            {
                "contrato_id": r.contrato_id,
                "janela": r.janela,
                "status": r.status,
                "recipients": r.recipients,
                "resend_message_id": r.resend_message_id,
                "error_message": r.error_message,
            }
            for r in summary.results
        ],  # type: ignore[arg-type]
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_financeiro_contratos.py -v`
Expected: PASS

- [ ] **Step 6: Ruff + full suite + commit**

```bash
cd apps/api && uv run --extra dev ruff check . && uv run --extra dev pytest -q
git add apps/api/app/modules/financeiro_contratos/ apps/api/tests/test_financeiro_contratos.py
git commit -m "feat(contratos): alertas de vencimento 30/15/7/0d idempotentes + dispatch manual"
```

---

### Task 6: Task Celery + beat diário

**Files:**
- Modify: `apps/workers/worker/tasks/financeiro.py` (hoje stubs)
- Modify: `apps/workers/worker/main.py` (1 entrada no beat_schedule)
- Test: `apps/workers/tests/test_financeiro_tasks.py` (novo)

**Interfaces:**
- Consumes: `dispatch_contrato_alerts` (Task 5) via lazy import (padrão do worker: `apps/api` no PYTHONPATH), `ResendClient`, `SessionLocal`, `get_settings`.
- Produces: task `worker.tasks.financeiro.dispatch_contrato_alerts` com destinatários da env `CONTRATOS_ALERT_EMAILS` (CSV) e override por argumento; entrada beat `contrato-alerts-daily` às 08h15 (5min após afastamentos, 08h10 — escalonamento do burst no Resend, padrão do repo).

- [ ] **Step 1: Write the failing test**

```python
# apps/workers/tests/test_financeiro_tasks.py
"""Smoke da task de alertas de contrato (Squad 5)."""

from __future__ import annotations

from worker.main import celery_app


def test_task_registrada() -> None:
    assert "worker.tasks.financeiro.dispatch_contrato_alerts" in celery_app.tasks


def test_beat_schedule_tem_contrato_alerts() -> None:
    entry = celery_app.conf.beat_schedule.get("contrato-alerts-daily")
    assert entry is not None
    assert entry["task"] == "worker.tasks.financeiro.dispatch_contrato_alerts"


def test_sem_env_retorna_erro_explicativo(monkeypatch) -> None:
    """Sem CONTRATOS_ALERT_EMAILS a task nao explode -- devolve dict de
    erro (mesmo contrato da task de certidoes)."""
    monkeypatch.delenv("CONTRATOS_ALERT_EMAILS", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    from worker.tasks.financeiro import dispatch_contrato_alerts

    result = dispatch_contrato_alerts.run()
    assert "error" in result
    assert "CONTRATOS_ALERT_EMAILS" in str(result["error"])
```

Nota: rodar os testes do worker do jeito que `apps/workers/tests/test_correlation_propagation.py` roda (verificar se há um `conftest.py`/comando próprio em apps/workers — usar o mesmo runner; tipicamente `cd apps/workers && uv run --extra dev pytest tests/ -v` ou o venv local `.venv`). Se `RESEND_API_KEY` não estiver setada, o caminho de erro muda — por isso o monkeypatch seta a key e o assert é na env de recipients (checar a ordem dos guards na implementação abaixo: settings primeiro, recipients depois — igual ao `_run_certidao_alerts`; ajustar o teste se necessário para o guard que vier primeiro).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/workers && uv run pytest tests/test_financeiro_tasks.py -v` (ajustar ao runner do worker)
Expected: FAIL — task não registrada / beat entry ausente

- [ ] **Step 3: Implement the worker task**

```python
# append em apps/workers/worker/tasks/financeiro.py
import asyncio
import os


@celery_app.task(name="worker.tasks.financeiro.dispatch_contrato_alerts")
def dispatch_contrato_alerts(recipients: list[str] | None = None) -> dict[str, object]:
    """Alertas de vencimento de contratos (Squad 5, demanda #12).

    Roda 1x/dia via beat. Destinatarios vem de CONTRATOS_ALERT_EMAILS
    (CSV) -- mesmo desenho de CERTIDOES_ALERT_EMAILS; `recipients`
    explicito e para testes manuais.
    """
    return asyncio.run(_run_contrato_alerts(recipients))


async def _run_contrato_alerts(
    recipients: list[str] | None,
) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.resend.client import ResendClient
        from app.modules.financeiro_contratos.alerts import (
            dispatch_contrato_alerts as _dispatch,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    if not settings.resend_api_key:
        return {"error": "RESEND_API_KEY not configured; skipping contrato alerts"}

    if recipients is None:
        env_val = os.getenv("CONTRATOS_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        return {"error": "CONTRATOS_ALERT_EMAILS not configured; nothing to send"}

    async with SessionLocal() as db:
        resend = ResendClient(api_key=settings.resend_api_key)
        try:
            summary = await _dispatch(db, resend, recipients=recipients)
        finally:
            await resend.aclose()
    return {
        "total_contratos": summary.total_contratos,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }
```

E a entrada no `beat_schedule` de `apps/workers/worker/main.py` (após `afastamento-alerts-daily`):

```python
    # Squad 5: alertas de vencimento de contratos 1x/dia (08h15).
    # 5min apos afastamentos (08h10) para escalonar o burst no Resend.
    # Idempotente via `contratos_alertas_log` (uniq contrato_id + janela).
    "contrato-alerts-daily": {
        "task": "worker.tasks.financeiro.dispatch_contrato_alerts",
        "schedule": crontab(hour="8", minute="15"),
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/workers && uv run pytest tests/test_financeiro_tasks.py -v` (mesmo runner do Step 2)
Expected: PASS

- [ ] **Step 5: Ruff + commit**

```bash
uv run --extra dev ruff check apps/workers 2>/dev/null || (cd apps/api && uv run --extra dev ruff check ../workers)
git add apps/workers/worker/tasks/financeiro.py apps/workers/worker/main.py apps/workers/tests/test_financeiro_tasks.py
git commit -m "feat(contratos): task Celery + beat diario 08h15 para alertas de vencimento"
```

---

### Task 7: UI — página /financeiro/contratos

**Files:**
- Create: `apps/web/src/app/(dashboard)/financeiro/contratos/page.tsx`
- Modify: `apps/web/src/app/(dashboard)/financeiro/page.tsx` (adicionar link/card para a nova página, seguindo o que a página já usa)
- Test: verificação manual + `npm run build` (o repo tem E2E Playwright só para a PWA de parte diária; não criar E2E novo aqui)

**Interfaces:**
- Consumes: rotas REST das Tasks 3–5; `apiFetch` de `@/lib/api`; padrão visual da página `licitacoes/certidoes/page.tsx` (server component + server actions + `revalidatePath`; badges tailwind; `formatDate` local).
- Produces: página com stat cards (Total / Vigentes / Vencendo ≤30d / Vencidos), form de cadastro, tabela com badge de vencimento e ações (encerrar, excluir), filtro por status/tipo via querystring.

- [ ] **Step 1: Write the page**

```tsx
// apps/web/src/app/(dashboard)/financeiro/contratos/page.tsx
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type Contrato = {
  id: number;
  titulo: string;
  contraparte_nome: string;
  contraparte_documento: string | null;
  tipo: string;
  obra_id: number | null;
  valor: number | null;
  data_inicio: string;
  data_fim: string | null;
  status: string;
  arquivo_path: string | null;
  easyjur_ref: string | null;
  observacoes: string | null;
  vencimento_status: string | null;
  dias_para_vencer: number | null;
  created_at: string;
  updated_at: string;
};

// Espelha TIPOS_CONTRATO / STATUS_CONTRATO do backend (duplicado de
// proposito -- mesmo racional da pagina de certidoes).
const TIPOS: Array<[string, string]> = [
  ["cliente", "Contrato com cliente"],
  ["fornecedor", "Contrato com fornecedor"],
  ["locacao", "Locação de equipamento"],
];
const STATUS: Array<[string, string]> = [
  ["rascunho", "Rascunho"],
  ["vigente", "Vigente"],
  ["encerrado", "Encerrado"],
  ["judicializado", "Judicializado"],
];

async function fetchContratos(
  params: { status?: string; tipo?: string } = {},
): Promise<Contrato[] | null> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.tipo) qs.set("tipo", params.tipo);
  const path = `/api/v1/financeiro/contratos${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<Contrato[]>(path);
  } catch {
    return null;
  }
}

async function createContrato(formData: FormData): Promise<void> {
  "use server";
  const titulo = String(formData.get("titulo") ?? "").trim();
  const contraparte_nome = String(formData.get("contraparte_nome") ?? "").trim();
  const tipo = String(formData.get("tipo") ?? "").trim();
  const data_inicio = String(formData.get("data_inicio") ?? "").trim();
  if (!titulo || !contraparte_nome || !tipo || !data_inicio) return;
  const valorReais = String(formData.get("valor") ?? "").trim();
  const payload: Record<string, unknown> = {
    titulo,
    contraparte_nome,
    tipo,
    data_inicio,
    contraparte_documento:
      String(formData.get("contraparte_documento") ?? "").trim() || null,
    // input em reais -> centavos (backend guarda int)
    valor: valorReais ? Math.round(parseFloat(valorReais.replace(",", ".")) * 100) : null,
    data_fim: String(formData.get("data_fim") ?? "").trim() || null,
    status: String(formData.get("status") ?? "rascunho").trim(),
    easyjur_ref: String(formData.get("easyjur_ref") ?? "").trim() || null,
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  await apiFetch("/api/v1/financeiro/contratos", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  revalidatePath("/financeiro/contratos");
}

async function encerrarContrato(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/financeiro/contratos/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "encerrado" }),
  });
  revalidatePath("/financeiro/contratos");
}

async function deleteContrato(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/financeiro/contratos/${id}`, { method: "DELETE" });
  revalidatePath("/financeiro/contratos");
}

function vencimentoBadge(c: Contrato): { label: string; className: string } {
  if (c.status === "encerrado")
    return { label: "Encerrado", className: "bg-slate-100 text-slate-700" };
  switch (c.vencimento_status) {
    case "vigente":
      return { label: "Vigente", className: "bg-emerald-100 text-emerald-800" };
    case "vencendo":
      return {
        label: `Vence em ${c.dias_para_vencer}d`,
        className: "bg-amber-100 text-amber-800",
      };
    case "vencido":
      return { label: "Vencido", className: "bg-rose-100 text-rose-800" };
    case "sem_validade":
      return { label: "Sem prazo", className: "bg-slate-100 text-slate-700" };
    default:
      return { label: "—", className: "bg-slate-100 text-slate-700" };
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  if (!y || !m || !d) return iso;
  return `${d}/${m}/${y}`;
}

function formatValor(centavos: number | null): string {
  if (centavos == null) return "—";
  return (centavos / 100).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

type SearchParams = { status?: string; tipo?: string };

export default async function ContratosPage(props: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await props.searchParams;
  const contratos = await fetchContratos(params);

  const counts = {
    total: contratos?.length ?? 0,
    vigente:
      contratos?.filter(
        (c) => c.status !== "encerrado" && c.vencimento_status === "vigente",
      ).length ?? 0,
    vencendo:
      contratos?.filter(
        (c) => c.status !== "encerrado" && c.vencimento_status === "vencendo",
      ).length ?? 0,
    vencido:
      contratos?.filter(
        (c) => c.status !== "encerrado" && c.vencimento_status === "vencido",
      ).length ?? 0,
  };

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Contratos</h1>
          <p className="mt-1 text-sm text-slate-500">
            Ciclo de contratos (cliente, fornecedor, locação) com alerta
            automático de vencimento por email (30, 15, 7 e 0 dias antes).
          </p>
        </div>
        <Link
          href="/financeiro"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Voltar
        </Link>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {(
          [
            ["Total", counts.total, "border-slate-200"],
            ["Vigentes", counts.vigente, "border-emerald-200"],
            ["Vencendo (≤30d)", counts.vencendo, "border-amber-300"],
            ["Vencidos", counts.vencido, "border-rose-300"],
          ] as Array<[string, number, string]>
        ).map(([label, value, border]) => (
          <div
            key={label}
            className={`rounded-xl border ${border} bg-white p-4`}
          >
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
          </div>
        ))}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-800">
          Cadastrar contrato
        </h2>
        <form
          action={createContrato}
          className="grid grid-cols-1 gap-3 md:grid-cols-2"
        >
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Título <span className="text-rose-500">*</span>
            <input
              name="titulo"
              required
              maxLength={255}
              placeholder="Locação de escavadeira CAT 320"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Contraparte <span className="text-rose-500">*</span>
            <input
              name="contraparte_nome"
              required
              maxLength={255}
              placeholder="TratorMax Ltda"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            CNPJ/CPF da contraparte
            <input
              name="contraparte_documento"
              maxLength={32}
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Tipo <span className="text-rose-500">*</span>
            <select
              name="tipo"
              required
              defaultValue=""
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="" disabled>
                — selecione —
              </option>
              {TIPOS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Valor (R$)
            <input
              name="valor"
              inputMode="decimal"
              placeholder="15000,00"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Status
            <select
              name="status"
              defaultValue="vigente"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              {STATUS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Início <span className="text-rose-500">*</span>
            <input
              name="data_inicio"
              type="date"
              required
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Fim (vazio = prazo indeterminado)
            <input
              name="data_fim"
              type="date"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Ref. EasyJur (se judicializado)
            <input
              name="easyjur_ref"
              maxLength={128}
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600 md:col-span-2">
            Observações
            <textarea
              name="observacoes"
              maxLength={2048}
              rows={2}
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <div className="md:col-span-2">
            <button
              type="submit"
              className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Cadastrar
            </button>
          </div>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-800">
            Contratos cadastrados
          </h2>
          <form className="flex items-center gap-2 text-xs">
            <select
              name="status"
              defaultValue={params.status ?? ""}
              className="rounded-md border border-slate-300 px-2 py-1"
            >
              <option value="">Todos os status</option>
              {STATUS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              name="tipo"
              defaultValue={params.tipo ?? ""}
              className="rounded-md border border-slate-300 px-2 py-1"
            >
              <option value="">Todos os tipos</option>
              {TIPOS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="rounded-md border border-slate-300 bg-white px-3 py-1 font-medium text-slate-700 hover:bg-slate-50"
            >
              Filtrar
            </button>
          </form>
        </div>
        {!contratos || contratos.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">
            {contratos === null
              ? "Não foi possível carregar os contratos (API fora do ar?)."
              : "Nenhum contrato cadastrado ainda."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">
                  <th className="py-2 pr-3">Título</th>
                  <th className="py-2 pr-3">Contraparte</th>
                  <th className="py-2 pr-3">Tipo</th>
                  <th className="py-2 pr-3">Valor</th>
                  <th className="py-2 pr-3">Fim</th>
                  <th className="py-2 pr-3">Vencimento</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {contratos.map((c) => {
                  const badge = vencimentoBadge(c);
                  return (
                    <tr key={c.id} className="border-b border-slate-100">
                      <td className="py-2 pr-3 font-medium text-slate-800">
                        {c.titulo}
                        {c.easyjur_ref ? (
                          <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700">
                            EasyJur {c.easyjur_ref}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3">{c.contraparte_nome}</td>
                      <td className="py-2 pr-3">
                        {TIPOS.find(([v]) => v === c.tipo)?.[1] ?? c.tipo}
                      </td>
                      <td className="py-2 pr-3">{formatValor(c.valor)}</td>
                      <td className="py-2 pr-3">{formatDate(c.data_fim)}</td>
                      <td className="py-2 pr-3">
                        <span
                          className={`rounded px-2 py-0.5 text-xs font-semibold ${badge.className}`}
                        >
                          {badge.label}
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        {STATUS.find(([v]) => v === c.status)?.[1] ?? c.status}
                      </td>
                      <td className="py-2 text-right">
                        {c.status !== "encerrado" ? (
                          <form action={encerrarContrato} className="inline">
                            <input type="hidden" name="id" value={c.id} />
                            <button
                              type="submit"
                              className="mr-2 text-xs font-medium text-slate-500 hover:text-slate-800"
                            >
                              Encerrar
                            </button>
                          </form>
                        ) : null}
                        <form action={deleteContrato} className="inline">
                          <input type="hidden" name="id" value={c.id} />
                          <button
                            type="submit"
                            className="text-xs font-medium text-rose-500 hover:text-rose-700"
                          >
                            Excluir
                          </button>
                        </form>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
```

Antes de finalizar: abrir `apps/web/src/app/(dashboard)/financeiro/page.tsx` e adicionar um link/card "Contratos" apontando para `/financeiro/contratos`, imitando exatamente o markup dos cards/links que a página já tiver (não inventar componente novo).

- [ ] **Step 2: Build para validar tipos/lint**

Run: `cd apps/web && npm run build`
Expected: build verde (server actions e tipos ok)

- [ ] **Step 3: Verificação manual (se docker/dev local disponível)**

Run: subir API + web como no fluxo de dev do repo e abrir `http://localhost:3000/financeiro/contratos`; criar 1 contrato com `data_fim` em 10 dias e conferir badge "Vence em 10d".
Expected: página renderiza, cadastro funciona, badge correto. (Sem ambiente local — o dono da sessão não roda Docker no Mac — pular e deixar para o smoke da demo Vercel.)

- [ ] **Step 4: Commit**

```bash
git add "apps/web/src/app/(dashboard)/financeiro/"
git commit -m "feat(contratos): pagina /financeiro/contratos com stat cards, cadastro e badges"
```

---

## Riscos e decisões registradas

1. **`valor` em centavos (int)** — evita float; a UI converte reais↔centavos. Verificar na revisão se outros módulos do repo usam outra convenção monetária e, se sim, seguir a existente.
2. **Reuso de `compute_status`/`janela_for_certidao` do D.6** — acopla Squad 5 ao módulo licitacoes. Aceitável: são funções puras e estáveis; alternativa (copiar) violaria DRY no 2º uso. O loop de dispatch, ao contrário, foi COPIADO deliberadamente (regras de skip diferentes; generalizar só no 3º uso).
3. **Migration `down_revision`** — head calculado na data do plano (`b8c9d0e1f2a3`); squads paralelas podem mudá-lo. Passo explícito de recálculo incluído.
4. **Mock do Resend nos testes** — o helper `_fake_resend` assume `ResendClient` com `_client` httpx substituível; o implementador deve conferir a técnica real usada em `test_licitacoes_certidoes.py` e seguir a de lá.
5. **Runner de testes do worker** — `apps/workers` tem venv próprio; usar o mesmo comando dos testes existentes de lá.
6. **Assinatura digital / EasyJur** — fora do escopo; entrada futura documentada (colunas aditivas nullable; `easyjur_ref` texto livre como ponte).
7. **Sem RBAC por módulo nos endpoints** — usado só `get_current_user` (paridade com certidões/fiscal). Se o repo tiver `module_roles` aplicado em algum router do financeiro, seguir o mais restritivo na revisão.
