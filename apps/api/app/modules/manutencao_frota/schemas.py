"""Pydantic schemas do modulo Frota (B.1 + B.3)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.manutencao_frota.models import (
    STATUSES_VALIDOS,
    TIPOS_DOC_VALIDOS,
    UFS_DETRAN_SUPORTADAS,
)
from app.modules.manutencao_frota.validators import (
    is_valid_chassi,
    is_valid_placa,
    is_valid_renavam,
    normalize_chassi,
    normalize_placa,
    normalize_renavam,
)


def _ensure_status_canonico(v: str | None) -> str | None:
    """Validador compartilhado entre Create e Update.

    Rejeita explicitamente `None` -- a coluna `frota_veiculos.status`
    e NOT NULL, e Pydantic v2 (`validate_default=False`) so chama o
    validador quando o campo aparece no payload, entao o caminho
    "campo nao enviado" no PATCH continua passando direto pelo
    `exclude_unset=True` no router. Quem manda `{"status": null}`
    explicito ganha 422 aqui em vez de IntegrityError 500 no commit.
    """
    if v is None or not isinstance(v, str) or v not in STATUSES_VALIDOS:
        raise ValueError(
            "status deve ser um de: " + ", ".join(sorted(STATUSES_VALIDOS))
        )
    return v


def _ensure_tipo_doc(v: str) -> str:
    if v not in TIPOS_DOC_VALIDOS:
        raise ValueError(
            "tipo deve ser um de: " + ", ".join(sorted(TIPOS_DOC_VALIDOS))
        )
    return v


class ModuleStatus(BaseModel):
    module: str
    implemented: bool
    stub: bool = False


# --- Documento do veiculo ---------------------------------------------------


class DocumentoVeiculoBase(BaseModel):
    tipo: str = Field(..., description="crlv|seguro|ipva|licenciamento|dpvat|outro")
    numero: str | None = None
    emissao: date | None = None
    validade: date | None = None
    valor: Decimal | None = None
    anexo_path: str | None = None
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str) -> str:
        return _ensure_tipo_doc(v)


class DocumentoVeiculoCreate(DocumentoVeiculoBase):
    pass


class DocumentoVeiculoUpdate(BaseModel):
    tipo: str | None = None
    numero: str | None = None
    emissao: date | None = None
    validade: date | None = None
    valor: Decimal | None = None
    anexo_path: str | None = None
    observacoes: str | None = None

    @field_validator("tipo")
    @classmethod
    def _validate_tipo(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _ensure_tipo_doc(v)


class DocumentoVeiculoRead(DocumentoVeiculoBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    veiculo_id: int
    source: str
    created_at: datetime
    updated_at: datetime


# --- Veiculo CRUD -----------------------------------------------------------


class VeiculoBase(BaseModel):
    placa: str = Field(..., min_length=7, max_length=8)
    renavam: str | None = None
    chassi: str | None = None
    marca: str | None = None
    modelo: str | None = None
    ano_fabricacao: int | None = Field(default=None, ge=1900, le=2100)
    ano_modelo: int | None = Field(default=None, ge=1900, le=2100)
    cor: str | None = None
    tipo: str | None = None  # texto livre -- frota mistura tudo
    combustivel: str | None = None
    obra: str | None = None
    setor: str | None = None
    km_atual: int | None = Field(default=None, ge=0)
    status: str | None = "ativo"
    data_aquisicao: date | None = None
    data_baixa: date | None = None
    observacoes: str | None = None

    @field_validator("placa")
    @classmethod
    def _validate_placa(cls, v: str) -> str:
        norm = normalize_placa(v)
        if not is_valid_placa(norm):
            raise ValueError(
                "placa invalida -- esperado AAA0000 (antiga) ou AAA0A00 (Mercosul)"
            )
        return norm

    @field_validator("renavam")
    @classmethod
    def _validate_renavam(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        norm = normalize_renavam(v)
        if not is_valid_renavam(norm):
            raise ValueError("renavam invalido (DV nao bate)")
        return norm

    @field_validator("chassi")
    @classmethod
    def _validate_chassi(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        norm = normalize_chassi(v)
        if not is_valid_chassi(norm):
            raise ValueError(
                "chassi invalido -- esperado 17 alfanumericos (ISO 3779)"
            )
        return norm

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        return _ensure_status_canonico(v)


class VeiculoCreate(VeiculoBase):
    documentos: list[DocumentoVeiculoCreate] | None = None


class VeiculoUpdate(BaseModel):
    """Update parcial -- placa/renavam/chassi excluidos (chaves
    naturais; trocas viram nova row + audit_log).
    """

    marca: str | None = None
    modelo: str | None = None
    ano_fabricacao: int | None = Field(default=None, ge=1900, le=2100)
    ano_modelo: int | None = Field(default=None, ge=1900, le=2100)
    cor: str | None = None
    tipo: str | None = None
    combustivel: str | None = None
    obra: str | None = None
    setor: str | None = None
    km_atual: int | None = Field(default=None, ge=0)
    status: str | None = None
    data_aquisicao: date | None = None
    data_baixa: date | None = None
    observacoes: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        return _ensure_status_canonico(v)


class VeiculoRead(VeiculoBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
    documentos: list[DocumentoVeiculoRead] = []


class VeiculoListResponse(BaseModel):
    items: list[VeiculoRead]
    total: int
    page: int
    page_size: int


# --- B.3 -- Consulta Detran (Infosimples) -----------------------------------


class ConsultaDetranRequest(BaseModel):
    uf: str = Field(..., min_length=2, max_length=2)

    @field_validator("uf")
    @classmethod
    def _validate_uf(cls, v: str) -> str:
        v_norm = v.strip().upper()
        if v_norm not in UFS_DETRAN_SUPORTADAS:
            raise ValueError(
                "uf nao suportada -- "
                + ", ".join(sorted(UFS_DETRAN_SUPORTADAS))
            )
        return v_norm


class ConsultaDetranRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    veiculo_id: int
    placa: str
    uf: str
    status: str
    source: str
    payload: dict[str, Any] | None = None
    error_msg: str | None = None
    executed_at: datetime


class ConsultaDetranListResponse(BaseModel):
    items: list[ConsultaDetranRead]
    total: int


# --- B.2 -- Parte Diaria (OCR via Document AI) -----------------------------


def _ensure_parte_status(v: str) -> str:
    from app.modules.manutencao_frota.models import PARTE_STATUSES

    if v not in PARTE_STATUSES:
        raise ValueError(
            "ocr_status deve ser um de: " + ", ".join(sorted(PARTE_STATUSES))
        )
    return v


class ParteDiariaUpdate(BaseModel):
    """Revisao manual dos campos extraidos pelo OCR.

    Todos opcionais -- o operador corrige so o que precisar. `ocr_status`
    deixa marcar `revisado` apos conferencia.
    """

    data: date | None = None
    veiculo_id: int | None = None
    operador: str | None = Field(default=None, max_length=200)
    obra: str | None = Field(default=None, max_length=200)
    equipamento: str | None = Field(default=None, max_length=200)
    placa: str | None = Field(default=None, max_length=8)
    horimetro_inicio: Decimal | None = None
    horimetro_fim: Decimal | None = None
    km_inicio: int | None = None
    km_fim: int | None = None
    observacoes: str | None = None
    ocr_status: str | None = None

    @field_validator("ocr_status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        return _ensure_parte_status(v) if v is not None else None


class ParteDiariaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    anexo_path: str | None = None
    filename_original: str | None = None
    mime_type: str | None = None
    data: date | None = None
    veiculo_id: int | None = None
    operador: str | None = None
    obra: str | None = None
    equipamento: str | None = None
    placa: str | None = None
    horimetro_inicio: Decimal | None = None
    horimetro_fim: Decimal | None = None
    km_inicio: int | None = None
    km_fim: int | None = None
    observacoes: str | None = None
    ocr_status: str
    ocr_source: str
    ocr_confidence: Decimal | None = None
    ocr_payload: dict[str, Any] | None = None
    ocr_error_msg: str | None = None
    created_at: datetime
    updated_at: datetime


class ParteDiariaListResponse(BaseModel):
    items: list[ParteDiariaRead]
    total: int
    page: int
    page_size: int
