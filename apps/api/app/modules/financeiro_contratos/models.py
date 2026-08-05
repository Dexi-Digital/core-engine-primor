"""Modelos do Modulo C -- ciclo de contratos (demanda #12).

Espelha o desenho do D.6 (certidoes): tabela principal + tabela de log
de alertas com UniqueConstraint (contrato_id, janela) garantindo
idempotencia do cron diario.

Decisao registrada (2026-08-04): assinatura digital fica fora ate o
provider ser definido. Quando entrar, sao 2 colunas nullable novas
(`assinatura_provider`, `assinatura_status`) -- migration aditiva,
nenhum campo atual muda. NAO criar as colunas agora (YAGNI).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Tipos e status canonicos. String livre no banco (mesmo racional das
# certidoes: adicionar valor novo sem migration); UI e validacao usam
# estas listas.
TIPOS_CONTRATO: tuple[tuple[str, str], ...] = (
    ("cliente", "Contrato com cliente"),
    ("fornecedor", "Contrato com fornecedor"),
    ("locacao", "Locacao de equipamento"),
)
TIPOS_CONTRATO_VALIDOS: frozenset[str] = frozenset(t for t, _ in TIPOS_CONTRATO)

STATUS_CONTRATO: tuple[tuple[str, str], ...] = (
    ("rascunho", "Rascunho"),
    ("vigente", "Vigente"),
    ("encerrado", "Encerrado"),
    ("judicializado", "Judicializado"),
)
STATUS_CONTRATO_VALIDOS: frozenset[str] = frozenset(s for s, _ in STATUS_CONTRATO)


class Contrato(Base):
    """Um contrato da Primor (cliente, fornecedor ou locacao).

    `valor` em `Numeric(20, 2)` -- mesma convencao monetaria do restante
    do repo (`fiscal.DocumentoFiscal.valor_total`, `licitacoes`,
    `manutencao_frota`, `dp_sesmt`): `Decimal`, nunca `float`.
    `data_fim` nullable: contrato por prazo indeterminado nao gera alerta
    de vencimento (mesmo tratamento de certidao sem validade no D.6).

    `easyjur_ref` e referencia textual livre (numero do processo /
    codigo interno EasyJur) enquanto a integracao real nao existe.
    """

    __tablename__ = "contratos"

    id: Mapped[int] = mapped_column(primary_key=True)
    titulo: Mapped[str] = mapped_column(String(255))
    contraparte_nome: Mapped[str] = mapped_column(String(255), index=True)
    contraparte_documento: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    tipo: Mapped[str] = mapped_column(String(32), index=True)
    obra_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("obras_obra.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    valor: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    data_inicio: Mapped[date] = mapped_column(Date)
    data_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="rascunho", index=True)
    arquivo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    easyjur_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_contratos_status_data_fim", "status", "data_fim"),
    )


class ContratoAlertaLog(Base):
    """Log de alertas de vencimento enviados por janela (30d/15d/7d/0d).

    Mesmo contrato de idempotencia do `CertidaoAlertaLog`: o cron diario
    nunca reenvia a mesma janela do mesmo contrato -- UniqueConstraint
    (contrato_id, janela) impoe isso no banco. Entradas `failed` sao
    atualizadas in-place no retry.
    """

    __tablename__ = "contratos_alertas_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    contrato_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contratos.id", ondelete="CASCADE"),
        index=True,
    )
    janela: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    recipients: Mapped[list[str]] = mapped_column(JSON)
    resend_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="sent")
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint("contrato_id", "janela", name="uq_contrato_alerta_janela"),
    )
