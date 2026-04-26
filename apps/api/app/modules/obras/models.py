"""ORM models para Obras (D1).

Uma `Obra` representa um canteiro/contrato da Primor: codigo interno,
cliente, localizacao, datas de inicio e encerramento previsto. Tem
relacao 1:N com `ObraDocumento` (ART/RRT, alvara, PCMAT, CIPA, RNC,
RIA, medicao, diario de obra, checklist alojamento/vivencia).

Partes diarias e medicoes financeiras sao tabelas proprias dos seus
modulos (manutencao_frota.partes_diarias / financeiro_*); o motor de
diagnostico (`app.modules.diagnostico`) consulta elas diretamente.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# --- canonical statuses ----------------------------------------------------

STATUS_ATIVA = "ativa"
STATUS_ENCERRADA = "encerrada"
STATUS_SUSPENSA = "suspensa"
STATUSES_VALIDOS = frozenset({STATUS_ATIVA, STATUS_ENCERRADA, STATUS_SUSPENSA})

# --- doc types canonicos (lista ZAG/PRIMOR) -------------------------------

DOC_ART = "ART"  # ART/RRT do CREA/CONFEA
DOC_RRT = "RRT"
DOC_ALVARA = "ALVARA"
DOC_ARTEX = "ARTEX"  # ART de execucao
DOC_PCMAT = "PCMAT"  # NR-18 -- obrigatorio em obras com >= 20 trabalhadores
DOC_PGR_OBRA = "PGR_OBRA"  # NR-1 -- substitui PCMAT em obras menores
DOC_CIPA_OBRA = "CIPA_OBRA"
DOC_DIARIO_OBRA = "DIARIO_OBRA"
DOC_MEDICAO = "MEDICAO"
DOC_CHECKLIST_ALOJAMENTO = "CHECKLIST_ALOJAMENTO"
DOC_CHECKLIST_VIVENCIA = "CHECKLIST_VIVENCIA"
DOC_LAUDO_BANHEIRO_QUIMICO = "LAUDO_BANHEIRO_QUIMICO"
DOC_RNC = "RNC"  # Relatorio de Nao Conformidade
DOC_RIA = "RIA"  # Relatorio de Investigacao de Acidentes
DOC_OUTRO = "OUTRO"

DOC_TIPOS_VALIDOS: frozenset[str] = frozenset(
    {
        DOC_ART,
        DOC_RRT,
        DOC_ALVARA,
        DOC_ARTEX,
        DOC_PCMAT,
        DOC_PGR_OBRA,
        DOC_CIPA_OBRA,
        DOC_DIARIO_OBRA,
        DOC_MEDICAO,
        DOC_CHECKLIST_ALOJAMENTO,
        DOC_CHECKLIST_VIVENCIA,
        DOC_LAUDO_BANHEIRO_QUIMICO,
        DOC_RNC,
        DOC_RIA,
        DOC_OUTRO,
    }
)

# Labels human-readable para UI / export.
DOC_LABELS: dict[str, str] = {
    DOC_ART: "ART (Anotacao de Responsabilidade Tecnica)",
    DOC_RRT: "RRT (Registro de Responsabilidade Tecnica)",
    DOC_ALVARA: "Alvara de construcao",
    DOC_ARTEX: "ART de execucao (ARTEx)",
    DOC_PCMAT: "PCMAT (NR-18)",
    DOC_PGR_OBRA: "PGR da obra (NR-1)",
    DOC_CIPA_OBRA: "CIPA da obra",
    DOC_DIARIO_OBRA: "Diario de obra",
    DOC_MEDICAO: "Medicao",
    DOC_CHECKLIST_ALOJAMENTO: "Checklist de alojamento",
    DOC_CHECKLIST_VIVENCIA: "Checklist de areas de vivencia",
    DOC_LAUDO_BANHEIRO_QUIMICO: "Laudo de banheiro quimico",
    DOC_RNC: "Relatorio de Nao Conformidade (RNC)",
    DOC_RIA: "Relatorio de Investigacao de Acidentes (RIA)",
    DOC_OUTRO: "Outro",
}


class Obra(Base):
    __tablename__ = "obras_obra"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(32), index=True)
    nome: Mapped[str] = mapped_column(String(255))
    cliente: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    cidade: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default=STATUS_ATIVA,
        server_default=STATUS_ATIVA,
        index=True,
    )
    data_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    encerramento_previsto: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    data_encerramento: Mapped[date | None] = mapped_column(Date, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    documentos: Mapped[list[ObraDocumento]] = relationship(
        back_populates="obra", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("codigo", name="uq_obras_obra_codigo"),)


class ObraDocumento(Base):
    __tablename__ = "obras_documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obra_id: Mapped[int] = mapped_column(
        ForeignKey("obras_obra.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(64), index=True)
    numero: Mapped[str | None] = mapped_column(String(128), nullable=True)
    emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    validade: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )
    orgao_emissor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anexo_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), default="manual", server_default="manual"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    obra: Mapped[Obra] = relationship(back_populates="documentos")
