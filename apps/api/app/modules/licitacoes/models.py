"""ORM models for the Licitacoes module."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Licitacao(Base):
    """A single procurement published at PNCP.

    `external_id` is a stable `cnpj-ano-sequencial` key used for upserts.
    `raw` keeps the original PNCP payload for auditing and future re-parsing.
    """

    __tablename__ = "licitacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="pncp", index=True)

    numero_compra: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ano_compra: Mapped[int | None] = mapped_column(nullable=True, index=True)
    sequencial_compra: Mapped[int | None] = mapped_column(nullable=True)

    objeto_compra: Mapped[str | None] = mapped_column(String, nullable=True)
    modalidade_nome: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    modo_disputa_nome: Mapped[str | None] = mapped_column(String(128), nullable=True)
    situacao_compra_nome: Mapped[str | None] = mapped_column(String(128), nullable=True)

    valor_total_estimado: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    valor_total_homologado: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    srp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    orgao_cnpj: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    orgao_razao_social: Mapped[str | None] = mapped_column(String(512), nullable=True)

    uf_sigla: Mapped[str | None] = mapped_column(String(4), nullable=True, index=True)
    municipio_nome: Mapped[str | None] = mapped_column(String(128), nullable=True)
    codigo_ibge: Mapped[str | None] = mapped_column(String(16), nullable=True)

    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    data_atualizacao_pncp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_licitacoes_uf_modalidade", "uf_sigla", "modalidade_nome"),
        Index("ix_licitacoes_publicacao_uf", "data_publicacao_pncp", "uf_sigla"),
    )
