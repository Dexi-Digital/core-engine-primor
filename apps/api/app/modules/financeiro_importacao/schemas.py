"""Contratos HTTP para lotes e consultas financeiras."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PrepararImportacao(BaseModel):
    nome_arquivo: str = Field(min_length=1, max_length=255, pattern=r"(?i)\.xlsx$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ConfirmarImportacao(BaseModel):
    substituir_lote_id: int | None = Field(default=None, gt=0)


class LoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome_arquivo: str
    origem: str
    status: str
    actor: str
    linhas: int
    erros: int
    avisos: int
    ocorrencias: list
    resumo: list
    mensagem: str | None
    criado_em: datetime
    atualizado_em: datetime


class Filtros(BaseModel):
    empresa: str | None = Field(default=None, max_length=64)
    local: str | None = Field(default=None, max_length=255)
    contraparte: str | None = Field(default=None, max_length=255)
    centro_custo: str | None = Field(default=None, max_length=255)
    natureza_i: str | None = Field(default=None, max_length=32)
    natureza_ii: str | None = Field(default=None, max_length=32)
    natureza_iii: str | None = Field(default=None, max_length=32)
    natureza: str | None = Field(default=None, max_length=255)
    classificacao: str | None = Field(default=None, max_length=100)
    campo_data: Literal["data_emissao", "competencia", "data_pagamento", "periodo"] = "competencia"
    inicio: date | None = None
    fim: date | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
