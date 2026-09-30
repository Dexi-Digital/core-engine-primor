"""Pydantic schemas para o motor de Diagnostico Documental (D1)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DiagnosticoRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    scope: str | None
    triggered_by: str | None
    total_findings: int
    ok_count: int
    ausente_count: int
    vencido_count: int
    vencendo_count: int
    summary_json: dict[str, Any] | None = None
    error_message: str | None = None


class DiagnosticoFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: int
    area: str
    entity_type: str
    entity_id: int | None
    entity_label: str
    doc_tipo: str
    doc_label: str | None
    status: str
    validade: date | None
    dias_para_vencimento: int | None
    message: str | None
    created_at: datetime


class DiagnosticoRunDetail(BaseModel):
    """Run + findings agrupados por area (UI usa direto)."""

    run: DiagnosticoRunRead
    findings_by_area: dict[str, list[DiagnosticoFindingRead]]


class DiagnosticoRunRequest(BaseModel):
    scope: str = "all"


class AtendimentoContagem(BaseModel):
    """Contagens + percentuais de um agrupamento (ver `atendimento.py`)."""

    total: int
    ok: int
    vencendo: int
    vencido: int
    ausente: int
    atendidos: int
    pendentes: int
    # None quando total == 0 (nada exigido).
    pct_atendimento: float | None
    pct_em_dia: float | None


class AtendimentoPorArea(AtendimentoContagem):
    area: str


class AtendimentoPorTipo(AtendimentoContagem):
    entity_type: str


class AtendimentoPorEntidade(AtendimentoContagem):
    entity_type: str
    entity_id: int | None
    entity_label: str
    areas: list[str]


class AtendimentoReport(BaseModel):
    run_id: int
    criterio: str
    geral: AtendimentoContagem
    por_area: list[AtendimentoPorArea]
    por_tipo_entidade: list[AtendimentoPorTipo]
    por_entidade: list[AtendimentoPorEntidade]
