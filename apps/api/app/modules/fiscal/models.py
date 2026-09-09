"""ORM model para documentos fiscais."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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

# Tipos suportados pela API Dominio. Mapeiam 1:1 para os exemplos da
# Central do Desenvolvedor (NF-e, NFS-e, NFC-e, CT-e, CF-e, Baixa de
# Parcela). frozenset para evitar mutacao acidental, igual ao padrao
# de STATUSES_VALIDOS no modulo dp_sesmt.
TIPOS_VALIDOS: frozenset[str] = frozenset(
    {"nfe", "nfse", "nfce", "cte", "cfe", "baixa"}
)
# `bloqueado`: o envio foi recusado pelo guard `ONVIO_ALLOW_SEND`, que
# protege o Dominio de PRODUCAO do escritorio contabil. E estado
# distinto de `erro` de proposito -- nao conta como falha e nao entra
# na fila de retry, porque retentar nao resolve.
STATUS_ENVIO_VALIDOS: frozenset[str] = frozenset(
    {"pendente", "enviando", "enviado", "erro", "bloqueado"}
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

    # --- 2o passe NF-e/NFC-e (parse_nfe_detalhes). Nulos para os demais
    # tipos e para documentos importados antes da feature (use o
    # endpoint /reprocessar para preencher retroativamente).
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    chave_dv_valida: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    valor_icms: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    valor_ipi: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    valor_pis: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    valor_cofins: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    # Vinculo manual com obra (rastreabilidade de custo). SET NULL: a
    # exclusao de uma obra nao pode apagar documento fiscal (auditoria).
    obra_id: Mapped[int | None] = mapped_column(
        ForeignKey("obras_obra.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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


class DocumentoFiscalItem(Base):
    """Um item (<det>) de NF-e/NFC-e. Filha de DocumentoFiscal.

    CASCADE no delete: item nao existe sem a nota (o delete da nota ja
    audita o snapshot; itens nao precisam de trilha propria).
    """

    __tablename__ = "fiscal_documento_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("fiscal_documentos.id", ondelete="CASCADE"), index=True
    )
    ordem: Mapped[int] = mapped_column(Integer)
    codigo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    descricao: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ncm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cfop: Mapped[str | None] = mapped_column(String(8), nullable=True)
    unidade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantidade: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4), nullable=True
    )
    valor_unitario: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    valor_total: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "documento_id", "ordem", name="uq_fiscal_item_documento_ordem"
        ),
    )
