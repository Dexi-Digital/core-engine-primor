"""ORM models para o motor de Diagnostico Documental (D1).

Um `DiagnosticoRun` e um snapshot de uma execucao do motor: percorre
todas as entidades cadastradas (funcionarios, veiculos, empresas,
obras), avalia o checklist por area aplicando regras condicionais
(flags em `Employee`), e grava 1 row por (entidade, doc obrigatorio)
em `DiagnosticoFinding`.

Status possiveis em finding:
- `ok`        -> doc presente e dentro da validade
- `vencendo`  -> presente, validade em <= 30 dias
- `vencido`   -> presente, validade no passado
- `ausente`   -> sem row no banco para esse doc obrigatorio
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Status da execucao do run
RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_ERROR = "error"
RUN_STATUSES = frozenset({RUN_RUNNING, RUN_DONE, RUN_ERROR})

# Areas cobertas pelo checklist
AREA_DP = "dp"
AREA_SST = "sst"
AREA_FROTA = "frota"
AREA_EMPRESA = "empresa"
AREA_OBRA = "obra"
AREAS = (AREA_DP, AREA_SST, AREA_FROTA, AREA_EMPRESA, AREA_OBRA)

# Status do finding
FINDING_OK = "ok"
FINDING_VENCENDO = "vencendo"
FINDING_VENCIDO = "vencido"
FINDING_AUSENTE = "ausente"
FINDING_STATUSES = frozenset(
    {FINDING_OK, FINDING_VENCENDO, FINDING_VENCIDO, FINDING_AUSENTE}
)

# Janela default para `vencendo` (em dias)
VENCENDO_THRESHOLD_DIAS = 30


class DiagnosticoRun(Base):
    __tablename__ = "diagnostico_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=RUN_RUNNING, server_default=RUN_RUNNING
    )
    scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_findings: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    ok_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    ausente_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    vencido_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    vencendo_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list[DiagnosticoFinding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DiagnosticoFinding(Base):
    __tablename__ = "diagnostico_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostico_runs.id", ondelete="CASCADE"), index=True
    )
    area: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    entity_label: Mapped[str] = mapped_column(String(255))
    doc_tipo: Mapped[str] = mapped_column(String(64), index=True)
    doc_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    validade: Mapped[_date | None] = mapped_column(Date, nullable=True)
    dias_para_vencimento: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[DiagnosticoRun] = relationship(back_populates="findings")
