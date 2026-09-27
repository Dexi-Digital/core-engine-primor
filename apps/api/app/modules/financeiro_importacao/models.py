"""Lotes preservados e apropriações; só a base ativa entra nas consultas."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ImportacaoFinanceira(Base):
    __tablename__ = "financeiro_importacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    origem: Mapped[str] = mapped_column(String(32), default="sistema90")
    status: Mapped[str] = mapped_column(String(32), default="aguardando_arquivo")
    actor: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str | None] = mapped_column(Text)
    export_path: Mapped[str | None] = mapped_column(Text)
    linhas: Mapped[int] = mapped_column(default=0)
    erros: Mapped[int] = mapped_column(default=0)
    avisos: Mapped[int] = mapped_column(default=0)
    ocorrencias: Mapped[list] = mapped_column(JSON, default=list)
    resumo: Mapped[list] = mapped_column(JSON, default=list)
    mensagem: Mapped[str | None] = mapped_column(String(500))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BaseFinanceira(Base):
    """Ponteiro singleton, bloqueado ao confirmar uma nova versão completa."""

    __tablename__ = "financeiro_base_ativa"

    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int | None] = mapped_column(ForeignKey("financeiro_importacoes.id"))


class ApropriacaoFinanceira(Base):
    __tablename__ = "financeiro_apropriacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("financeiro_importacoes.id"), index=True)
    linha: Mapped[int]
    empresa: Mapped[str] = mapped_column(String(64), index=True)
    contraparte: Mapped[str | None] = mapped_column(Text)
    titulo: Mapped[str] = mapped_column(String(255))
    data_pagamento: Mapped[date | None] = mapped_column(Date)
    periodo: Mapped[date | None] = mapped_column(Date)
    data_inclusao_nf: Mapped[date | None] = mapped_column(Date)
    data_emissao: Mapped[date | None] = mapped_column(Date, index=True)
    valor_original: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_incremento: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_abatimento: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_amortizado: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_acrescimo: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_deducao: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_pago: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    valor_atual: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    local: Mapped[str | None] = mapped_column(String(255), index=True)
    centro_custo: Mapped[str | None] = mapped_column(String(255))
    id_natureza: Mapped[str | None] = mapped_column(String(255))
    natureza_i: Mapped[str | None] = mapped_column(String(32))
    natureza_ii: Mapped[str | None] = mapped_column(String(32))
    natureza_iii: Mapped[str | None] = mapped_column(String(32))
    natureza: Mapped[str | None] = mapped_column(Text)
    valor_apropriado: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    competencia: Mapped[date | None] = mapped_column(Date, index=True)
    classificacao: Mapped[str | None] = mapped_column(String(100), index=True)
    raw: Mapped[dict] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("lote_id", "linha", name="uq_financeiro_lote_linha"),)
