"""ORM model para documentos fiscais."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Tipos suportados pela API Dominio. Mapeiam 1:1 para os exemplos da
# Central do Desenvolvedor (NF-e, NFS-e, NFC-e, CT-e, CF-e, Baixa de
# Parcela). frozenset para evitar mutacao acidental, igual ao padrao
# de STATUSES_VALIDOS no modulo dp_sesmt.
TIPOS_VALIDOS: frozenset[str] = frozenset(
    {"nfe", "nfse", "nfce", "cte", "cfe", "baixa"}
)
STATUS_ENVIO_VALIDOS: frozenset[str] = frozenset(
    {"pendente", "enviando", "enviado", "erro"}
)


class DocumentoFiscal(Base):
    """Um XML fiscal recebido pela plataforma.

    `xml_path` aponta para o arquivo bruto no storage abstraido (mesmo
    `EditaisStorage` usado pelo D.4 -- local FS ou OneDrive). O hash
    SHA-256 do conteudo serve como deduplicacao para CF-e/Baixa onde
    a `chave_acesso` nao existe ou nao e universal.
    """

    __tablename__ = "fiscal_documentos"

    id: Mapped[int] = mapped_column(primary_key=True)

    tipo: Mapped[str] = mapped_column(String(16), index=True)
    chave_acesso: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    numero: Mapped[str | None] = mapped_column(String(32), nullable=True)
    serie: Mapped[str | None] = mapped_column(String(8), nullable=True)

    emitente_cnpj: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    emitente_nome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destinatario_cnpj: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    destinatario_nome: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    valor_total: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    data_emissao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    xml_path: Mapped[str] = mapped_column(String(1024))
    xml_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    status_envio: Mapped[str] = mapped_column(
        String(16), default="pendente", index=True
    )
    protocolo_dominio: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_msg: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tipo", "chave_acesso", name="uq_fiscal_tipo_chave"),
        UniqueConstraint("xml_hash", name="uq_fiscal_xml_hash"),
        Index("ix_fiscal_documentos_emissao_tipo", "data_emissao", "tipo"),
    )
