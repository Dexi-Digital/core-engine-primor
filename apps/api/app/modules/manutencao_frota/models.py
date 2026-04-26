"""ORM models para o Modulo B (Frota / Manutencao).

B.1 cobre cadastro de veiculos + documentos (CRLV, seguro, IPVA,
licenciamento). B.2 adiciona `partes_diarias` (OCR de log de
operacao via Google Document AI). B.3 consulta Detran via
Infosimples e materializa multas/IPVA/CRLV em `frota_documentos` com
`source` distinguindo manual/automatico.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Status canonicos -- frozenset pra os schemas validarem sem duplicar.
STATUS_ATIVO = "ativo"
STATUS_MANUTENCAO = "manutencao"
STATUS_BAIXADO = "baixado"
STATUS_VENDIDO = "vendido"
STATUSES_VALIDOS = frozenset(
    {STATUS_ATIVO, STATUS_MANUTENCAO, STATUS_BAIXADO, STATUS_VENDIDO}
)

# Tipos de documento que rastreamos por veiculo.
DOC_CRLV = "crlv"
DOC_SEGURO = "seguro"
DOC_IPVA = "ipva"
DOC_LICENCIAMENTO = "licenciamento"
DOC_DPVAT = "dpvat"  # extinto a partir de 2021 mas pode existir histórico
DOC_OUTRO = "outro"
TIPOS_DOC_VALIDOS = frozenset(
    {DOC_CRLV, DOC_SEGURO, DOC_IPVA, DOC_LICENCIAMENTO, DOC_DPVAT, DOC_OUTRO}
)

SOURCE_MANUAL = "manual"
SOURCE_DETRAN_RPA = "detran_rpa"  # B.3: consulta Infosimples
SOURCE_OCR = "ocr"  # B.2: parte diaria via Document AI

# Status do OCR de uma parte diaria (B.2).
PARTE_PENDENTE = "pendente"  # upload feito, worker nao processou
PARTE_PROCESSADO = "processado"  # campos extraidos pelo OCR
PARTE_REVISADO = "revisado"  # operador conferiu/corrigiu
PARTE_ERRO = "erro"  # Document AI falhou
PARTE_STATUSES = frozenset(
    {PARTE_PENDENTE, PARTE_PROCESSADO, PARTE_REVISADO, PARTE_ERRO}
)

# Status de uma consulta Detran (Infosimples).
CONSULTA_PENDENTE = "pendente"
CONSULTA_OK = "ok"
CONSULTA_ERRO = "erro"
CONSULTA_MOCK = "mock"
CONSULTA_STATUSES = frozenset(
    {CONSULTA_PENDENTE, CONSULTA_OK, CONSULTA_ERRO, CONSULTA_MOCK}
)

UFS_DETRAN_SUPORTADAS = frozenset({"SP", "MG", "GO"})


class Veiculo(Base):
    """Veiculo da frota da Primor.

    `placa` e a chave natural mas NAO e unique-constraint -- trocas de
    placa (Mercosul, sinistro) sao gerenciadas por nova row + audit_log
    em vez de IntegrityError. `renavam` e mais estavel mas opcional
    porque cadastros legados podem nao ter.
    """

    __tablename__ = "frota_veiculos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- identificacao ---
    placa: Mapped[str] = mapped_column(String(8), index=True)
    renavam: Mapped[str | None] = mapped_column(String(11), nullable=True, index=True)
    chassi: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)

    # --- ficha tecnica ---
    marca: Mapped[str | None] = mapped_column(String(64), nullable=True)
    modelo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ano_fabricacao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ano_modelo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tipo: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    combustivel: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- alocacao operacional ---
    obra: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    setor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    km_atual: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- status do veiculo ---
    status: Mapped[str] = mapped_column(
        String(16),
        default=STATUS_ATIVO,
        server_default=STATUS_ATIVO,
        index=True,
    )
    data_aquisicao: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_baixa: Mapped[date | None] = mapped_column(Date, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- metadata ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # --- relations ---
    documentos: Mapped[list[DocumentoVeiculo]] = relationship(
        back_populates="veiculo",
        cascade="all, delete-orphan",
    )
    consultas_detran: Mapped[list[ConsultaDetran]] = relationship(
        back_populates="veiculo",
        cascade="all, delete-orphan",
    )
    partes_diarias: Mapped[list[ParteDiaria]] = relationship(
        back_populates="veiculo",
        # ondelete=SET NULL no DB; aqui nao deleta orfaos (parte diaria
        # tem valor de auditoria mesmo sem o veiculo).
        passive_deletes=True,
    )


class DocumentoVeiculo(Base):
    """Documento do veiculo (CRLV, seguro, IPVA, licenciamento).

    Cada tipo pode ter multiplas rows (historico anual). A UI mostra
    sempre o mais recente por tipo, mas mantemos o historico para
    auditoria + relatorios.
    """

    __tablename__ = "frota_documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veiculo_id: Mapped[int] = mapped_column(
        ForeignKey("frota_veiculos.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(32), index=True)
    numero: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    validade: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    anexo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), default=SOURCE_MANUAL, server_default=SOURCE_MANUAL
    )

    # --- Sync OneDrive (D1 fase 2) -- ver dp_sesmt.EmployeeDocument ---
    onedrive_item_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    onedrive_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    onedrive_last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onedrive_sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("onedrive_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    veiculo: Mapped[Veiculo] = relationship(back_populates="documentos")


class ConsultaDetran(Base):
    """Log de uma consulta Detran (via Infosimples) de um veiculo.

    Cada chamada gera uma row -- mesmo placa, mesma UF, mesmo dia.
    Isso permite (a) auditar quanto gastamos em consultas pagas, (b)
    reconstruir progressao de multas/debitos no tempo, (c) reprocessar
    payloads sem precisar bater novamente na API. `payload` armazena
    a resposta normalizada do client; `error_msg` e populado quando
    `status='erro'`.
    """

    __tablename__ = "frota_consultas_detran"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veiculo_id: Mapped[int] = mapped_column(
        ForeignKey("frota_veiculos.id", ondelete="CASCADE"), index=True
    )
    placa: Mapped[str] = mapped_column(String(8), index=True)
    uf: Mapped[str] = mapped_column(String(2), index=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default=CONSULTA_PENDENTE,
        server_default=CONSULTA_PENDENTE,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        default="infosimples_mock",
        server_default="infosimples_mock",
    )
    # JSON normalizado (mesma forma para SP/MG/GO). UI consome direto.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    veiculo: Mapped[Veiculo] = relationship(back_populates="consultas_detran")


class ParteDiaria(Base):
    """Parte diaria do equipamento -- log diario de operacao com OCR.

    Workflow:
      1. Operador faz upload do PDF/foto da parte diaria escaneada
         -> row criada com `ocr_status='pendente'`.
      2. Worker async chama Document AI -> popula `data`, `operador`,
         `obra`, `equipamento`, `horimetro_*`, `km_*`, `ocr_payload`,
         `ocr_status='processado'`.
      3. UI permite revisao manual dos campos -> `ocr_status='revisado'`.
      4. Em caso de falha do Document AI -> `ocr_status='erro'` +
         `ocr_error_msg`. UI permite reprocessar.

    Campos extraidos sao todos nullable porque o OCR pode nao reconhecer.
    """

    __tablename__ = "partes_diarias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Anexo (PDF/JPG/PNG escaneado).
    anexo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    filename_original: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Campos extraidos do OCR (todos revisaveis).
    data: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    veiculo_id: Mapped[int | None] = mapped_column(
        ForeignKey("frota_veiculos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    operador: Mapped[str | None] = mapped_column(String(200), nullable=True)
    obra: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    equipamento: Mapped[str | None] = mapped_column(String(200), nullable=True)
    placa: Mapped[str | None] = mapped_column(String(8), nullable=True)
    horimetro_inicio: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    horimetro_fim: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    km_inicio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    km_fim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Abastecimento informado pelo apontador em campo (PWA mobile, D5).
    # Nullable porque nem todo apontamento envolve abastecimento.
    combustivel_litros: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    combustivel_custo: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # UUID do PWA para idempotencia (D5 fase 2). Unique parcial em
    # PG via migration; SQLite (testes) tolera duplicatas pois a
    # logica de dedup roda no service antes do insert.
    client_uuid: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status + payload do OCR.
    ocr_status: Mapped[str] = mapped_column(
        String(16),
        default=PARTE_PENDENTE,
        server_default=PARTE_PENDENTE,
        index=True,
    )
    ocr_source: Mapped[str] = mapped_column(
        String(32),
        default="google_documentai_mock",
        server_default="google_documentai_mock",
    )
    ocr_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    ocr_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ocr_error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    veiculo: Mapped[Veiculo | None] = relationship(back_populates="partes_diarias")
