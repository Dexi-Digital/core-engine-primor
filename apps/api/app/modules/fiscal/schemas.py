"""Pydantic v2 schemas para o modulo fiscal."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.fiscal.models import STATUS_ENVIO_VALIDOS, TIPOS_VALIDOS


class DocumentoFiscalRead(BaseModel):
    """View para listagem/detalhe."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: str
    chave_acesso: str | None = None
    numero: str | None = None
    serie: str | None = None
    emitente_cnpj: str | None = None
    emitente_nome: str | None = None
    destinatario_cnpj: str | None = None
    destinatario_nome: str | None = None
    valor_total: Decimal | None = None
    data_emissao: datetime | None = None
    xml_path: str
    xml_hash: str | None = None
    status_envio: str
    protocolo_dominio: str | None = None
    sent_at: datetime | None = None
    error_msg: str | None = None
    retry_count: int
    source: str | None = None
    observacoes: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentoFiscalUpdate(BaseModel):
    """Update parcial. So observacoes e status sao mutaveis pela UI;
    metadata (chave, valor, partes) vem do XML e nao deve ser editavel
    -- editar quebra a auditoria fiscal."""

    observacoes: str | None = None
    status_envio: str | None = None

    @field_validator("status_envio")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in STATUS_ENVIO_VALIDOS:
            raise ValueError(
                f"status_envio invalido: {v!r}. "
                f"Validos: {sorted(STATUS_ENVIO_VALIDOS)}"
            )
        return v


class DocumentoFiscalEnvioResponse(BaseModel):
    """Resposta do POST /enviar-dominio."""

    documento_id: int
    status_envio: str
    enqueued: bool
    protocolo_dominio: str | None = None
    error_msg: str | None = None


class DocumentoFiscalListFilter(BaseModel):
    """Filtros do GET /documentos. Reune queries para validacao."""

    tipo: str | None = Field(default=None)
    status_envio: str | None = Field(default=None)
    emitente_cnpj: str | None = Field(default=None)
    destinatario_cnpj: str | None = Field(default=None)
    search: str | None = Field(default=None, description="busca em chave/numero")

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in TIPOS_VALIDOS:
            raise ValueError(
                f"tipo invalido: {v!r}. Validos: {sorted(TIPOS_VALIDOS)}"
            )
        return v

    @field_validator("status_envio")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in STATUS_ENVIO_VALIDOS:
            raise ValueError(
                f"status_envio invalido: {v!r}. "
                f"Validos: {sorted(STATUS_ENVIO_VALIDOS)}"
            )
        return v
