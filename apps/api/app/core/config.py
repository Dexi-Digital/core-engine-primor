"""Application settings loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Motor Central API"
    app_version: str = "0.1.0"
    environment: str = Field(default="development")

    database_url: str = Field(
        default="postgresql+asyncpg://primor:primor@localhost:5432/primor",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # JWT (auth real -- ver app.modules.auth). O default `change-me-in-production`
    # e DEV-only e dispara warning no startup. Troque o `secret_key` no
    # .env de prod antes de subir; do contrario qualquer atacante com
    # acesso ao codigo pode forjar tokens.
    secret_key: str = Field(default="change-me-in-production")
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Seed do primeiro admin (idempotente -- ver
    # app.modules.auth.startup.ensure_admin_seed). Cadastros subsequentes
    # sao feitos pelo proprio admin pela UI / API.
    admin_email: str | None = Field(default=None)
    admin_password: str | None = Field(default=None)
    admin_nome: str = Field(default="Administrador")

    # Rate limit do POST /api/v1/auth/login (bucket Redis por
    # (email, ip)). Desabilitado em testes por default para nao exigir
    # Redis rodando -- em prod/staging manter habilitado. Ver
    # app.modules.auth.rate_limit.
    login_rate_limit_enabled: bool = Field(default=True)
    login_rate_limit_max_attempts: int = Field(default=5)
    login_rate_limit_window_s: int = Field(default=60)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Object storage (MinIO / S3).
    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="minio")
    s3_secret_key: str = Field(default="minio123")
    s3_bucket: str = Field(default="primor-docs")

    # Outbound email (Resend). When resend_api_key is unset, the adapter
    # raises instead of silently dropping -- avoids losing boletins in prod.
    resend_api_key: str | None = Field(default=None)
    resend_from_email: str = Field(default="Motor Central <boletins@motorcentral.dev>")
    public_base_url: str = Field(default="http://localhost:3000")

    # Edital storage root (D.4). Local filesystem em dev, volume montado
    # em docker, e substituido pelo backend OneDrive/S3 quando
    # `storage_backend != "local"`. O default em `/tmp` so funciona em
    # dev: `/tmp` e wiped reiniciando containers; em prod com
    # `STORAGE_BACKEND=local` voce TEM que setar `EDITAIS_STORAGE_PATH`
    # para um volume persistente (ex: `/data/motor-central/editais`).
    # Veja docs/deployment-option-a.md.
    editais_storage_path: str = Field(default="/tmp/motor-central/editais")

    # LLM providers for D.5 (analise de edital). The cost-routed wrapper
    # picks the cheapest configured provider first and falls back to the
    # next one on error. If none is configured the API returns 503.
    anthropic_api_key: str | None = Field(default=None)
    anthropic_model: str = Field(default="claude-3-5-haiku-20241022")
    openai_api_key: str | None = Field(default=None)
    openai_model: str = Field(default="gpt-4.1-nano")

    # DirectData (consulta paga de CPF para o dossie de admissao do
    # Modulo A). Sem chave, o adapter opera em modo mock para
    # desbloquear desenvolvimento -- ver app/integrations/directdata.
    directdata_api_key: str | None = Field(default=None)

    # Microsoft Graph / OneDrive (storage de documentos: anexos de
    # edital, ASOs, contratos). Mesmo padrao do DirectData/LLM: sem
    # credenciais, o adapter opera em modo mock determinístico para
    # desbloquear desenvolvimento -- ver app/integrations/onedrive.
    # `storage_backend` controla qual backend `EditaisStorage` o
    # router monta: "local" (default) ou "onedrive".
    storage_backend: str = Field(default="local")
    ms_graph_tenant_id: str | None = Field(default=None)
    ms_graph_client_id: str | None = Field(default=None)
    ms_graph_client_secret: str | None = Field(default=None)
    ms_graph_drive_id: str | None = Field(default=None)
    # Subpasta no drive onde os anexos de licitacao vao parar.
    # Mantemos uma raiz dedicada para isolar os arquivos da plataforma
    # de outros conteudos do tenant.
    ms_graph_root_folder: str = Field(default="MotorCentral/editais")

    # Dominio Sistemas - Central do Desenvolvedor (Modulo C). Sem
    # credenciais o adapter cai no DominioMockClient deterministico
    # para nao bloquear dev/CI -- igual DirectData/LLM/OneDrive.
    # `audit_url` e `integracao` identificam o escritorio contabil
    # parceiro dentro do tenant Dominio.
    dominio_audit_url: str | None = Field(default=None)
    dominio_integracao: str | None = Field(default=None)
    dominio_client_id: str | None = Field(default=None)
    dominio_client_secret: str | None = Field(default=None)
    dominio_base_url: str = Field(
        default="https://api.dominioexterior.com.br/api/v1"
    )
    # Subpasta no storage onde os XMLs vao parar (usa a mesma
    # `EditaisStorage` -- local FS ou OneDrive). Mantemos uma raiz
    # dedicada para isolar por modulo.
    fiscal_storage_subdir: str = Field(default="MotorCentral/fiscal")

    # Infosimples (Modulo B.3 -- consulta Detran SP/MG/GO por placa).
    # Cobre multas, IPVA, licenciamento, restricoes. Sem token o
    # adapter cai no `InfosimplesMockClient` deterministico para nao
    # bloquear dev/CI -- mesmo padrao DirectData/LLM/OneDrive/Dominio.
    # Pricing: ~R$0.50-2.00 por consulta no plano da Infosimples.
    infosimples_token: str | None = Field(default=None)
    infosimples_base_url: str = Field(default="https://api.infosimples.com")

    # Google Document AI (Modulo B.2 -- OCR de Parte Diaria).
    # Sem credenciais (qualquer um dos 3 vazio), o adapter cai no
    # `GoogleDocumentAIMockClient` -- mesmo padrao Infosimples/Dominio.
    # `credentials_json` e o JSON inteiro da service account, em uma
    # unica string (escape de `\n` no env e ok). `processor_id` e o
    # identificador do processor criado no console Document AI.
    google_documentai_credentials_json: str | None = Field(default=None)
    gcp_project_id: str | None = Field(default=None)
    documentai_processor_id: str | None = Field(default=None)
    documentai_location: str = Field(default="us")
    # Subpasta no storage onde os anexos das partes diarias ficam.
    parte_diaria_storage_subdir: str = Field(
        default="MotorCentral/partes-diarias"
    )

    # OnSafety (Modulo A -- SST: ASOs, fichas de EPI, treinamentos).
    # Sem token o adapter cai em modo mock deterministico -- mesmo
    # padrao DirectData/Infosimples/Dominio. ATENCAO: tokens nao sao
    # intercambiaveis entre ambientes (token de producao devolve 401
    # em api.dev.*); o default e homologacao, producao exige trocar
    # a base_url para https://api.onsafety.com.br explicitamente.
    onsafety_token: str | None = Field(default=None)
    onsafety_base_url: str = Field(default="https://api.dev.onsafety.com.br")
    # Guard-rail: escrita (create_or_update) contra api.onsafety.com.br
    # (PRODUCAO) so com opt-in explicito. O unico token disponivel hoje
    # e o de prod (ADR-001) -- este flag impede que um dev com o token
    # no .env crie trabalhadores reais sem querer. Leitura nao e afetada.
    onsafety_allow_prod_write: bool = Field(default=False)
    # Estabelecimento/projeto OnSafety ao qual o push de onboarding
    # vincula o trabalhador (obrigatorio para o create_or_update deles:
    # sem projeto a API recusa com 403 "Estabelecimento não
    # especificado"). Um id global por enquanto; mapeamento
    # obra->projeto fica para quando o Modulo A tiver obras na OnSafety.
    onsafety_projeto_id: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    return Settings()
