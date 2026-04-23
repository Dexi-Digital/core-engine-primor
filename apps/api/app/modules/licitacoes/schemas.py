from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LicitacaoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    source: str
    numero_compra: str | None
    ano_compra: int | None
    sequencial_compra: int | None
    objeto_compra: str | None
    modalidade_nome: str | None
    modo_disputa_nome: str | None
    situacao_compra_nome: str | None
    valor_total_estimado: Decimal | None
    valor_total_homologado: Decimal | None
    srp: bool | None
    orgao_cnpj: str | None
    orgao_razao_social: str | None
    uf_sigla: str | None
    municipio_nome: str | None
    codigo_ibge: str | None
    data_publicacao_pncp: datetime | None
    data_atualizacao_pncp: datetime | None


class LicitacaoListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    data: list[LicitacaoRead]


class IngestResult(BaseModel):
    inserted: int
    updated: int
    skipped: int
    total_fetched: int
