"""Pydantic schemas para Obras (D1)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.obras.models import (
    DOC_TIPOS_VALIDOS,
    STATUSES_VALIDOS,
)


class ObraBase(BaseModel):
    codigo: str = Field(..., max_length=32)
    nome: str = Field(..., max_length=255)
    cliente: str | None = Field(default=None, max_length=255)
    uf: str | None = Field(default=None, max_length=2)
    cidade: str | None = Field(default=None, max_length=128)
    status: str = Field(default="ativa", max_length=16)
    data_inicio: date | None = None
    encerramento_previsto: date | None = None
    data_encerramento: date | None = None
    observacoes: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in STATUSES_VALIDOS:
            raise ValueError(
                "status deve ser um de: " + ", ".join(sorted(STATUSES_VALIDOS))
            )
        return v


class ObraCreate(ObraBase):
    pass


class ObraUpdate(BaseModel):
    codigo: str | None = Field(default=None, max_length=32)
    nome: str | None = Field(default=None, max_length=255)
    cliente: str | None = Field(default=None, max_length=255)
    uf: str | None = Field(default=None, max_length=2)
    cidade: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=16)
    data_inicio: date | None = None
    encerramento_previsto: date | None = None
    data_encerramento: date | None = None
    observacoes: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in STATUSES_VALIDOS:
            raise ValueError(
                "status deve ser um de: " + ", ".join(sorted(STATUSES_VALIDOS))
            )
        return v


class ObraRead(ObraBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class ObraDocumentoBase(BaseModel):
    tipo: str = Field(..., max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    orgao_emissor: str | None = Field(default=None, max_length=255)
    anexo_path: str | None = Field(default=None, max_length=1024)
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str) -> str:
        if v not in DOC_TIPOS_VALIDOS:
            raise ValueError(
                "tipo deve ser um de: " + ", ".join(sorted(DOC_TIPOS_VALIDOS))
            )
        return v


class ObraDocumentoCreate(ObraDocumentoBase):
    pass


class ObraDocumentoUpdate(BaseModel):
    tipo: str | None = Field(default=None, max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    orgao_emissor: str | None = Field(default=None, max_length=255)
    anexo_path: str | None = Field(default=None, max_length=1024)
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str | None) -> str | None:
        if v is not None and v not in DOC_TIPOS_VALIDOS:
            raise ValueError(
                "tipo deve ser um de: " + ", ".join(sorted(DOC_TIPOS_VALIDOS))
            )
        return v


class ObraDocumentoRead(ObraDocumentoBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    obra_id: int
    source: str
    created_at: datetime
    updated_at: datetime
