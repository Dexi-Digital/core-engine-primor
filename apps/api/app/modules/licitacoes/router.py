"""Modulo D - Inteligencia de Licitacoes.

Escopo:
    - Scrapers B2G (PNCP primeiro; Conlicitacao, Diarios Oficiais no roadmap).
    - Analise de saude municipal (TCE, transparencia) -- futuro.
    - Busca documental de concorrentes (fase de habilitacao) -- futuro.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.integrations.llm.anthropic_client import AnthropicProvider
from app.integrations.llm.base import LLMError, LLMProvider, LLMUnavailableError
from app.integrations.llm.openai_client import OpenAIProvider
from app.integrations.llm.router import CostRoutedProvider
from app.integrations.pncp.client import PncpClient
from app.integrations.resend.client import ResendClient
from app.modules.dp_sesmt.schemas import ModuleStatus
from app.modules.licitacoes.analise import (
    analyze_edital_for_licitacao,
    get_analise,
)
from app.modules.licitacoes.boletins import (
    create_saved_query,
    delete_saved_query,
    dispatch_boletins,
    list_saved_queries,
)
from app.modules.licitacoes.editais import (
    download_edital_for_licitacao,
    get_edital,
    list_anexos,
)
from app.modules.licitacoes.schemas import (
    AnexoEditalRead,
    BoletimDispatchSummary,
    EditalAnaliseRead,
    EditalDownloadResult,
    EditalRead,
    IngestResult,
    LicitacaoListResponse,
    LicitacaoRead,
    SavedQueryCreate,
    SavedQueryRead,
)
from app.modules.licitacoes.service import (
    get_licitacao,
    ingest_publicacoes,
    list_licitacoes,
)
from app.modules.licitacoes.storage import EditaisStorage, LocalStorage

router = APIRouter()


def get_pncp_client() -> PncpClient:
    return PncpClient()


def get_editais_storage() -> EditaisStorage:
    """Default storage: local filesystem under `editais_storage_path`."""
    return LocalStorage(get_settings().editais_storage_path)


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Build a cost-routed LLM provider using whatever keys are configured.

    Raises `LLMUnavailableError` if neither Anthropic nor OpenAI is set --
    the router endpoint translates that into HTTP 503.
    """
    providers: list[LLMProvider] = []
    if settings.openai_api_key:
        providers.append(
            OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
        )
    if settings.anthropic_api_key:
        providers.append(
            AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
            )
        )
    if not providers:
        raise LLMUnavailableError(
            "Nenhuma chave LLM configurada (ANTHROPIC_API_KEY / OPENAI_API_KEY)."
        )
    return CostRoutedProvider(providers)  # type: ignore[return-value]


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency: returns a configured LLMProvider or raises 503."""
    try:
        return build_llm_provider(get_settings())
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="licitacoes", implemented=True)


@router.get("", response_model=LicitacaoListResponse)
async def list_endpoint(
    uf: str | None = Query(None, max_length=2),
    modalidade: str | None = None,
    orgao_cnpj: str | None = None,
    search: str | None = Query(
        None,
        description="Busca textual no objeto da compra (trigram no Postgres, ilike no SQLite).",
        max_length=200,
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> LicitacaoListResponse:
    items, total = await list_licitacoes(
        db,
        uf=uf,
        modalidade=modalidade,
        orgao_cnpj=orgao_cnpj,
        search=search,
        page=page,
        page_size=page_size,
    )
    return LicitacaoListResponse(
        total=total,
        page=page,
        page_size=page_size,
        data=[LicitacaoRead.model_validate(i) for i in items],
    )


@router.get("/{licitacao_id}", response_model=LicitacaoRead)
async def get_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
) -> LicitacaoRead:
    obj = await get_licitacao(db, licitacao_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Licitacao nao encontrada")
    return LicitacaoRead.model_validate(obj)


@router.post("/ingest/pncp", response_model=IngestResult)
async def ingest_endpoint(
    data_inicial: Annotated[date | None, Query(description="Default: 1 dia atras")] = None,
    data_final: Annotated[date | None, Query(description="Default: hoje")] = None,
    uf: str | None = Query(None, max_length=2),
    max_paginas: int | None = Query(None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    client: PncpClient = Depends(get_pncp_client),
) -> IngestResult:
    """Kick off a PNCP ingestion for the given window.

    Useful for small/on-demand pulls from the UI. Use the Celery task
    `worker.tasks.licitacoes.crawler_pncp` for scheduled / large windows.
    """
    today = date.today()
    data_final = data_final or today
    data_inicial = data_inicial or (data_final - timedelta(days=1))
    if data_inicial > data_final:
        raise HTTPException(status_code=400, detail="data_inicial > data_final")

    try:
        return await ingest_publicacoes(
            db,
            client,
            data_inicial=data_inicial,
            data_final=data_final,
            uf=uf,
            max_paginas=max_paginas,
        )
    finally:
        await client.aclose()


# --- D.3: boletins ---


@router.get("/boletins/saved-queries", response_model=list[SavedQueryRead])
async def list_saved_queries_endpoint(
    user_email: str | None = Query(None, max_length=255),
    db: AsyncSession = Depends(get_db),
) -> list[SavedQueryRead]:
    rows = await list_saved_queries(db, user_email=user_email)
    return [SavedQueryRead.model_validate(r) for r in rows]


@router.post("/boletins/saved-queries", response_model=SavedQueryRead, status_code=201)
async def create_saved_query_endpoint(
    payload: SavedQueryCreate,
    db: AsyncSession = Depends(get_db),
) -> SavedQueryRead:
    row = await create_saved_query(
        db,
        nome=payload.nome,
        user_email=str(payload.user_email),
        recipients=[str(r) for r in payload.recipients],
        uf=payload.uf,
        modalidade=payload.modalidade,
        search=payload.search,
        orgao_cnpj=payload.orgao_cnpj,
        active=payload.active,
    )
    return SavedQueryRead.model_validate(row)


@router.delete("/boletins/saved-queries/{saved_query_id}", status_code=204)
async def delete_saved_query_endpoint(
    saved_query_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await delete_saved_query(db, saved_query_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Saved query nao encontrada")


@router.post("/boletins/dispatch", response_model=BoletimDispatchSummary)
async def dispatch_boletins_endpoint(
    saved_query_id: int | None = Query(
        None, description="Se informado, despacha apenas essa query"
    ),
    db: AsyncSession = Depends(get_db),
) -> BoletimDispatchSummary:
    """On-demand dispatch. Normally triggered by Celery beat 3x/dia.

    Requires RESEND_API_KEY; returns 503 if not configured.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(
            status_code=503,
            detail="RESEND_API_KEY nao configurada; configure em settings para enviar boletins.",
        )

    resend = ResendClient(api_key=settings.resend_api_key)
    try:
        return await dispatch_boletins(
            db,
            resend,
            saved_query_ids=[saved_query_id] if saved_query_id else None,
        )
    finally:
        await resend.aclose()


# --- D.4: edital download ---


@router.get("/{licitacao_id}/edital", response_model=EditalRead)
async def get_edital_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
) -> EditalRead:
    edital = await get_edital(db, licitacao_id)
    if edital is None:
        raise HTTPException(status_code=404, detail="Edital ainda nao baixado")
    anexos = await list_anexos(db, edital.id)
    payload = EditalRead.model_validate(edital)
    payload.anexos = [AnexoEditalRead.model_validate(a) for a in anexos]
    return payload


@router.post(
    "/{licitacao_id}/edital/download",
    response_model=EditalDownloadResult,
)
async def download_edital_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
    pncp: PncpClient = Depends(get_pncp_client),
    storage: EditaisStorage = Depends(get_editais_storage),
) -> EditalDownloadResult:
    """Fetch edital + anexos from PNCP for `licitacao_id`. Idempotent.

    Triggers a synchronous download; for large windows use the worker task.
    """
    try:
        return await download_edital_for_licitacao(
            db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        await pncp.aclose()


# --- D.5: edital analise ---


@router.get(
    "/{licitacao_id}/edital/analise",
    response_model=EditalAnaliseRead,
)
async def get_edital_analise_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
) -> EditalAnaliseRead:
    edital = await get_edital(db, licitacao_id)
    if edital is None:
        raise HTTPException(
            status_code=404, detail="Edital ainda nao baixado (rode D.4 primeiro)"
        )
    analise = await get_analise(db, edital.id)
    if analise is None:
        raise HTTPException(status_code=404, detail="Analise nao executada")
    return EditalAnaliseRead.model_validate(analise)


@router.post(
    "/{licitacao_id}/edital/analise",
    response_model=EditalAnaliseRead,
)
async def run_edital_analise_endpoint(
    licitacao_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_editais_storage),
    llm: LLMProvider = Depends(get_llm_provider),
) -> EditalAnaliseRead:
    """Execute a LLM-based analysis of the downloaded edital.

    Returns 503 if no LLM provider is configured, 404 if D.4 hasn't run yet.
    Idempotent: re-running updates the single `edital_analise` row.
    """
    try:
        analise = await analyze_edital_for_licitacao(
            db,
            licitacao_id=licitacao_id,
            storage=storage,
            llm=llm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await llm.aclose()

    return EditalAnaliseRead.model_validate(analise)
