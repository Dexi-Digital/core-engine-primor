from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    failed_modalidades: list[int] = []


class SavedQueryCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    user_email: EmailStr
    recipients: list[EmailStr] = Field(min_length=1, max_length=20)
    uf: str | None = Field(default=None, max_length=2)
    modalidade: str | None = Field(default=None, max_length=128)
    search: str | None = Field(default=None, max_length=200)
    orgao_cnpj: str | None = Field(default=None, max_length=32)
    active: bool = True


class SavedQueryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    user_email: str
    recipients: list[str]
    uf: str | None
    modalidade: str | None
    search: str | None
    orgao_cnpj: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class BoletimDispatchResult(BaseModel):
    saved_query_id: int
    licitacoes_count: int
    last_licitacao_id: int | None
    status: str
    resend_message_id: str | None = None
    error_message: str | None = None


class BoletimDispatchSummary(BaseModel):
    total_queries: int
    sent: int
    skipped_empty: int
    failed: int
    results: list[BoletimDispatchResult]


class AnexoEditalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequencial_documento: int
    titulo: str | None
    tipo_documento: str | None
    source_url: str
    filename: str
    size_bytes: int | None
    content_type: str | None
    downloaded_at: datetime


class EditalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    licitacao_id: int
    source: str
    status: str
    anexos_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    anexos: list[AnexoEditalRead] = []


class EditalDownloadResult(BaseModel):
    licitacao_id: int
    source: str
    status: str  # "completed" | "failed" | "empty"
    anexos_count: int
    new_anexos: int
    error_message: str | None = None


class AtestadoCatRead(BaseModel):
    descricao: str
    quantidade_minima: str | None = None


class EditalAnaliseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    edital_id: int
    status: str  # "pending" | "completed" | "empty" | "failed"
    provider: str | None
    model: str | None
    prazo_execucao_dias: int | None
    garantia_percentual: Decimal | None
    bdi_maximo_percentual: Decimal | None
    visita_tecnica_obrigatoria: bool | None
    valor_estimado: Decimal | None
    data: dict | None
    anexos_analisados: int
    total_pages: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: Decimal | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


# --- D.6: certidoes / atestados ---


class CertidaoCreate(BaseModel):
    empresa_cnpj: str = Field(min_length=11, max_length=32)
    tipo: str = Field(min_length=1, max_length=64)
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    arquivo_path: str | None = Field(default=None, max_length=1024)
    orgao_emissor: str | None = Field(default=None, max_length=255)
    observacoes: str | None = Field(default=None, max_length=2048)


class CertidaoUpdate(BaseModel):
    numero: str | None = Field(default=None, max_length=128)
    emissao: date | None = None
    validade: date | None = None
    arquivo_path: str | None = Field(default=None, max_length=1024)
    orgao_emissor: str | None = Field(default=None, max_length=255)
    observacoes: str | None = Field(default=None, max_length=2048)


class CertidaoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    empresa_cnpj: str
    tipo: str
    numero: str | None
    emissao: date | None
    validade: date | None
    arquivo_path: str | None
    orgao_emissor: str | None
    observacoes: str | None
    created_at: datetime
    updated_at: datetime

    # Computed: vigente | vencendo | vencido | sem_validade.
    # Preenchido pelo router antes de devolver (compute_status).
    status_atual: str | None = None
    dias_para_vencer: int | None = None


class CertidaoAlertaResult(BaseModel):
    certidao_id: int
    janela: str
    status: str
    recipients: list[str]
    resend_message_id: str | None = None
    error_message: str | None = None


class CertidaoAlertaSummary(BaseModel):
    total_certidoes: int
    sent: int
    skipped: int
    failed: int
    results: list[CertidaoAlertaResult]


class CertidaoAlertaDispatchPayload(BaseModel):
    """Payload do POST /certidoes/dispatch-alerts.

    Os destinatarios sao informados a cada chamada para nao guardar email
    pessoal em settings (evita LGPD issue trivial). O cron do worker pode
    usar uma env var dedicada (CERTIDOES_ALERT_EMAILS).
    """

    recipients: list[EmailStr] = Field(min_length=1, max_length=20)
