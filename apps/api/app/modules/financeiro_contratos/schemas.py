"""Schemas do ciclo de contratos (Squad 5)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContratoCreate(BaseModel):
    titulo: str = Field(min_length=1, max_length=255)
    contraparte_nome: str = Field(min_length=1, max_length=255)
    contraparte_documento: str | None = Field(default=None, max_length=32)
    tipo: str = Field(min_length=1, max_length=32)
    obra_id: int | None = None
    valor: Decimal | None = Field(default=None, ge=0)
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
    valor: Decimal | None = Field(default=None, ge=0)
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
    valor: Decimal | None
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

    recipients: list[EmailStr] = Field(min_length=1, max_length=20)
