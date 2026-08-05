"""ORM models for the Licitacoes module."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
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

    # --- Captador Squad 1: triagem humana (ver triagem.py) -----------------
    # Valores validos em `triagem.STATUS_VALIDOS`; string livre no schema
    # para permitir novos status sem migracao (mesmo racional de
    # `CertidaoEmpresa.tipo`).
    status_triagem: Mapped[str] = mapped_column(
        String(32),
        default="novo_captado",
        server_default="novo_captado",
        index=True,
    )

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


class SavedQuery(Base):
    """User-saved filter for licitacao boletins (D.3).

    A user may register multiple saved queries (ex: "obras pesadas MG",
    "pregoes SP pavimentacao"). The scheduler picks active ones and sends
    digests to `recipients`.
    """

    __tablename__ = "licitacoes_saved_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    user_email: Mapped[str] = mapped_column(String(255), index=True)
    # Stored as JSON list of emails. MVP: no multi-user RBAC yet -- the
    # owner is `user_email`, `recipients` can add CCs (e.g. equipe licitacoes).
    recipients: Mapped[list[str]] = mapped_column(JSON)
    uf: Mapped[str | None] = mapped_column(String(4), nullable=True)
    modalidade: Mapped[str | None] = mapped_column(String(128), nullable=True)
    search: Mapped[str | None] = mapped_column(String(200), nullable=True)
    orgao_cnpj: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BoletimLog(Base):
    """Record of each boletim dispatch. `last_licitacao_id` is used to
    de-duplicate: the next run for the same saved query only includes rows
    with `id > last_licitacao_id`."""

    __tablename__ = "licitacoes_boletins_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    saved_query_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes_saved_queries.id", ondelete="CASCADE"),
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    licitacoes_count: Mapped[int] = mapped_column(Integer)
    last_licitacao_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resend_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="sent")
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        Index("ix_boletins_log_saved_query", "saved_query_id", "sent_at"),
    )


class Edital(Base):
    """Aggregated status of edital download for a given licitacao (D.4).

    One row per licitacao. `source` tells whether the files came from PNCP
    or from one of the fallback portals (comprasnet / licitacoes_e). The
    actual files go in `AnexoEdital` rows.
    """

    __tablename__ = "licitacoes_editais"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    # Where the files came from. "pncp" is the primary path; the others are
    # reserved for the fallback portals (see AGENTS.md / docs/integrations.md).
    source: Mapped[str] = mapped_column(String(32), default="pncp")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    anexos_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnexoEdital(Base):
    """One downloaded file (Edital, Termo de Referencia, Projetos, etc).

    `storage_path` is the opaque identifier returned by the storage
    backend -- a local filesystem path in dev, an S3/MinIO key in prod.
    Uniqueness on (edital_id, sequencial_documento) keeps the download
    step idempotent: re-running the task does not create duplicate rows.
    """

    __tablename__ = "licitacoes_editais_anexos"

    id: Mapped[int] = mapped_column(primary_key=True)
    edital_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes_editais.id", ondelete="CASCADE"),
        index=True,
    )
    sequencial_documento: Mapped[int] = mapped_column(Integer)
    titulo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tipo_documento: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("edital_id", "sequencial_documento", name="uq_anexo_seq"),
    )


class DecisaoTriagem(Base):
    """Trilha de decisoes da triagem humana do Captador (Squad 1).

    Uma row por acao da analista (aprovar / rejeitar / observacao).
    O motivo de rejeicao e obrigatorio na camada de servico; aqui a
    coluna e nullable porque aprovacao/observacao podem vir sem texto.
    """

    __tablename__ = "licitacoes_decisoes_triagem"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
    )
    # 'aprovado' | 'rejeitado' | 'observacao' (triagem.DECISAO_*)
    decisao: Mapped[str] = mapped_column(String(16))
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    usuario_email: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_decisoes_triagem_lic_criado", "licitacao_id", "created_at"),
    )


class EditalAnalise(Base):
    """Result of LLM-based structured extraction of an edital (D.5).

    One row per edital. Re-running the analysis updates the existing row
    in place (analise is idempotent by design: re-running against the
    same anexos should return roughly the same result).

    `data` keeps the whole extracted payload as JSON so new fields can be
    added without schema migrations. Top-level columns mirror the most
    queried fields for indexing / quick dashboards.
    """

    __tablename__ = "licitacoes_editais_analises"

    id: Mapped[int] = mapped_column(primary_key=True)
    edital_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes_editais.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", index=True
    )  # "pending" | "completed" | "failed" | "empty"
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Promoted fields (queryable) -- kept in sync with `data`.
    prazo_execucao_dias: Mapped[int | None] = mapped_column(Integer, nullable=True)
    garantia_percentual: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    bdi_maximo_percentual: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    visita_tecnica_obrigatoria: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    valor_estimado: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )

    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    anexos_analisados: Mapped[int] = mapped_column(Integer, default=0)
    total_pages: Mapped[int] = mapped_column(Integer, default=0)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CertidaoEmpresa(Base):
    """Documento da empresa Primor com possivel vencimento (D.6).

    Cobre CNDs (federal, estadual, municipal), FGTS, CNDT, INSS, atestados
    CAT, certidao de falencia, etc. Quando `validade` e None, a certidao
    e considerada "permanente" (atestados CAT nao vencem) e o cron de
    alerta a ignora.

    O arquivo opcional vai em storage (LocalStorage hoje, MinIO/S3 amanha)
    e `arquivo_path` e o handle opaco devolvido pelo storage.
    """

    __tablename__ = "certidoes_empresa"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_cnpj: Mapped[str] = mapped_column(String(32), index=True)
    # Tipo da certidao: usar `CertidaoTipo` no codigo, string aqui para
    # permitir adicionar tipos sem migracao. Ver `app.modules.licitacoes.certidoes`.
    tipo: Mapped[str] = mapped_column(String(64), index=True)
    numero: Mapped[str | None] = mapped_column(String(128), nullable=True)
    emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    validade: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    arquivo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    orgao_emissor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_certidoes_empresa_cnpj_tipo", "empresa_cnpj", "tipo"),
    )


class CertidaoAlertaLog(Base):
    """Registro de alertas enviados por janela (30d/15d/7d/0d).

    Garante idempotencia: o cron de alertas diario nao reenvia o mesmo
    email para a mesma janela da mesma certidao -- o UniqueConstraint
    (certidao_id, janela) impoe isso no banco.
    """

    __tablename__ = "certidoes_alertas_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    certidao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("certidoes_empresa.id", ondelete="CASCADE"),
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
        UniqueConstraint("certidao_id", "janela", name="uq_certidao_alerta_janela"),
    )


# --- D1: documentos societarios (separado de certidoes_empresa) -----------

DOC_EMPRESA_CONTRATO_SOCIAL = "CONTRATO_SOCIAL"
DOC_EMPRESA_ALTERACAO = "ALTERACAO_CONTRATUAL"
DOC_EMPRESA_BALANCO = "BALANCO_PATRIMONIAL"
DOC_EMPRESA_SICAF = "SICAF"
DOC_EMPRESA_CAGEF = "CAGEF"
DOC_EMPRESA_SUCAF = "SUCAF"
DOC_EMPRESA_OUTRO = "OUTRO"

DOC_EMPRESA_TIPOS_VALIDOS: frozenset[str] = frozenset(
    {
        DOC_EMPRESA_CONTRATO_SOCIAL,
        DOC_EMPRESA_ALTERACAO,
        DOC_EMPRESA_BALANCO,
        DOC_EMPRESA_SICAF,
        DOC_EMPRESA_CAGEF,
        DOC_EMPRESA_SUCAF,
        DOC_EMPRESA_OUTRO,
    }
)

DOC_EMPRESA_LABELS: dict[str, str] = {
    DOC_EMPRESA_CONTRATO_SOCIAL: "Contrato social",
    DOC_EMPRESA_ALTERACAO: "Alteracao contratual",
    DOC_EMPRESA_BALANCO: "Balanco patrimonial",
    DOC_EMPRESA_SICAF: "SICAF (cadastro federal)",
    DOC_EMPRESA_CAGEF: "CAGEF (cadastro estadual MG)",
    DOC_EMPRESA_SUCAF: "SUCAF (cadastro municipal BH)",
    DOC_EMPRESA_OUTRO: "Outro",
}


class EmpresaDocumento(Base):
    """Documento societario / cadastro oficial da empresa.

    Diferente de `CertidaoEmpresa` (CNDs/atestados, vencimento curto +
    alerta 30/15/7/0), aqui ficam docs perenes ou de longa validade:
    contrato social, alteracoes contratuais, balanco patrimonial,
    cadastros SICAF/CAGEF/SUCAF.
    """

    __tablename__ = "empresa_documentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_cnpj: Mapped[str] = mapped_column(String(20), index=True)
    tipo: Mapped[str] = mapped_column(String(64), index=True)
    numero: Mapped[str | None] = mapped_column(String(128), nullable=True)
    emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    validade: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    orgao_emissor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anexo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), default="manual", server_default="manual"
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

    __table_args__ = (
        Index("ix_empresa_documentos_cnpj_tipo", "empresa_cnpj", "tipo"),
    )


# --- D.6 fase 2: log de consultas CREA via Infosimples (PR #28) -----------

# Tipos de consulta suportados (espelha `CREA_TIPOS_SUPORTADOS` do client).
CREA_TIPO_ART = "art"
CREA_TIPO_PROFISSIONAL = "profissional"
CREA_TIPO_EMPRESA = "empresa"

CREA_CONSULTA_PENDENTE = "pendente"
CREA_CONSULTA_OK = "ok"
CREA_CONSULTA_ERRO = "erro"
CREA_CONSULTA_MOCK = "mock"


class ConsultaCrea(Base):
    """Log de uma consulta CREA (via Infosimples).

    Mesma estrategia da `ConsultaDetran` (PR #14):
      - cada chamada gera uma row independente (audit + cache)
      - `payload` guarda a resposta NORMALIZADA (UI consome direto)
      - `status` = 'pendente' | 'ok' | 'erro' | 'mock'

    Diferente do Detran: nao tem FK pra um veiculo especifico --
    consulta CREA pode ser ad-hoc (operador valida ART nova antes de
    cadastrar como certidao). Quando o operador clica "Importar como
    certidao" via `POST /importar-art`, criamos uma `CertidaoEmpresa`
    e gravamos `certidao_id` aqui pra rastreabilidade.
    """

    __tablename__ = "crea_consultas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), index=True)
    # 'art' | 'profissional' | 'empresa'
    tipo: Mapped[str] = mapped_column(String(16), index=True)
    # numero ART, registro CREA do profissional, ou CNPJ
    identificador: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default=CREA_CONSULTA_PENDENTE,
        server_default=CREA_CONSULTA_PENDENTE,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        default="infosimples_mock",
        server_default="infosimples_mock",
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    # FK opcional: setada quando consulta vira `CertidaoEmpresa`
    # (via `POST /importar-art`). Permite UI mostrar "ja importada".
    certidao_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("certidoes_empresa.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Captador Squad 2: pasta do projeto + planilha orcamentaria -----------


class PastaProjeto(Base):
    """Pasta do projeto criada no storage apos aprovacao na triagem.

    Uma por licitacao. `caminho` e o handle fisico (path local ou path
    Graph); `link_pasta` e a URL clicavel (webUrl do OneDrive) quando o
    backend fornece uma -- no backend local fica None e a Aba de
    Triagem cai no detalhe interno da licitacao.

    Decisao registrada no plano: a pasta fisica continua sendo
    `{licitacao_id}` (idempotencia do D.4); o slug humano
    `<uf>-<municipio>-<orgao>-<numero>` fica so em `nome_pasta`.
    """

    __tablename__ = "licitacoes_pastas_projeto"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    nome_pasta: Mapped[str] = mapped_column(String(255))
    caminho: Mapped[str] = mapped_column(String(1024))
    link_pasta: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    storage_backend: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(32), default="criada", server_default="criada"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlanilhaOrcamentaria(Base):
    """Anexo elegivel (XLSX/XLS/ODS) classificado como possivel planilha
    orcamentaria. A de maior score acima do limiar vira `principal=True`;
    a analista pode trocar via PATCH (`status_validacao=principal_manual`,
    que o reprocessamento respeita).
    """

    __tablename__ = "licitacoes_planilhas_orcamentarias"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        index=True,
    )
    anexo_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes_editais_anexos.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    extensao: Mapped[str] = mapped_column(String(16))
    score_classificacao: Mapped[int] = mapped_column(Integer, default=0)
    link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # "automatica" | "principal_manual" | "descartada"
    status_validacao: Mapped[str] = mapped_column(
        String(32), default="automatica", server_default="automatica"
    )
    principal: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Squad 3: resultados/homologacoes (secao 9 do Projeto Tecnico) ---------


class ResultadoLicitacao(Base):
    """Resultado homologado de um item de licitacao (fornecedor vencedor).

    Fonte: API portal do PNCP (`/itens/{n}/resultados`). Um item pode ter
    mais de um resultado (ordem de classificacao em SRP), por isso o
    unique inclui `sequencial_resultado`. Alimenta os dashboards de
    concorrentes e geotargeting.
    """

    __tablename__ = "licitacoes_resultados"

    id: Mapped[int] = mapped_column(primary_key=True)
    licitacao_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="CASCADE"),
        index=True,
    )
    item_numero: Mapped[int] = mapped_column(Integer)
    sequencial_resultado: Mapped[int] = mapped_column(Integer, default=1)
    cnpj_vencedor: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    razao_social: Mapped[str | None] = mapped_column(String(512), nullable=True)
    valor_homologado: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    valor_unitario: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    data_resultado: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    situacao: Mapped[str | None] = mapped_column(String(64), nullable=True)
    porte_fornecedor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "licitacao_id", "item_numero", "sequencial_resultado",
            name="uq_resultado_item_seq",
        ),
        Index("ix_resultados_cnpj_data", "cnpj_vencedor", "data_resultado"),
    )


class AtaRegistroPreco(Base):
    """Ata de Registro de Preco captada do PNCP (D.9 do roadmap).

    `numero_controle_pncp_ata` e a chave natural do PNCP (upsert
    idempotente). `licitacao_id` e preenchido quando o
    `numeroControlePNCPCompra` bate com uma licitacao ja captada.
    `valor` nao vem na API de atas -- fica NULL ate cruzarmos com a
    licitacao vinculada.
    """

    __tablename__ = "licitacoes_atas_rp"

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_controle_pncp_ata: Mapped[str] = mapped_column(
        String(128), unique=True, index=True
    )
    numero_ata: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ano_ata: Mapped[int | None] = mapped_column(Integer, nullable=True)
    licitacao_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("licitacoes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    orgao_cnpj: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    orgao_nome: Mapped[str | None] = mapped_column(String(512), nullable=True)
    objeto: Mapped[str | None] = mapped_column(Text, nullable=True)
    vigencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cancelado: Mapped[bool] = mapped_column(Boolean, default=False)
    possibilidade_adesao: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
