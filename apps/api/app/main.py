"""FastAPI entrypoint for the Motor Central API."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import CorrelationIdMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.auth.startup import ensure_admin_seed, warn_dev_secret
from app.modules.dp_sesmt.router import router as dp_sesmt_router
from app.modules.financeiro_contratos.router import router as financeiro_router
from app.modules.fiscal.router import router as fiscal_router
from app.modules.ia_tools.router import router as ia_tools_router
from app.modules.licitacoes.certidoes_router import router as certidoes_router
from app.modules.licitacoes.router import router as licitacoes_router
from app.modules.manutencao_frota.router import router as manutencao_router
from app.modules.observability.router import router as observability_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    # Auth startup: warning de secret default + seed do admin inicial
    # se ADMIN_EMAIL/ADMIN_PASSWORD vierem no env (idempotente).
    warn_dev_secret(settings)
    await ensure_admin_seed(settings)
    yield
    # Shutdown: fechar singletons que abriram pools TCP. Cada
    # `reset_*_singleton()` retorna a instancia anterior (ou None se
    # nunca foi tocada), e o `aclose()` libera o pool httpx.
    from app.modules.fiscal.service import reset_dominio_singleton
    from app.modules.manutencao_frota.service import (
        reset_celery_dispatcher_singleton,
        reset_documentai_singleton,
        reset_infosimples_singleton,
    )

    prev_dominio = reset_dominio_singleton()
    if prev_dominio is not None:
        await prev_dominio.aclose()

    prev_infosimples = reset_infosimples_singleton()
    if prev_infosimples is not None:
        await prev_infosimples.aclose()

    prev_documentai = reset_documentai_singleton()
    if prev_documentai is not None:
        await prev_documentai.aclose()

    # Celery dispatcher e sync (`Celery.close()`) -- libera pool de
    # conexoes Redis/AMQP que o `send_task` mantem aberto.
    prev_celery = reset_celery_dispatcher_singleton()
    if prev_celery is not None:
        prev_celery.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Motor Central ZAG/PRIMOR — camada de governanca e orquestracao.",
        lifespan=lifespan,
    )

    # Ordem importa: CORS por fora, correlation por dentro -- assim o
    # `correlation_id` ja esta bound quando o handler logga, e o
    # OPTIONS preflight do CORS nao consome o ID atoa. Starlette
    # processa middlewares em ordem inversa de registro (insert(0)),
    # entao a ULTIMA chamada a add_middleware vira a OUTERMOST.
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(dp_sesmt_router, prefix="/api/v1/dp-sesmt", tags=["dp-sesmt"])
    app.include_router(manutencao_router, prefix="/api/v1/manutencao-frota", tags=["manutencao-frota"])
    app.include_router(financeiro_router, prefix="/api/v1/financeiro", tags=["financeiro-contratos"])
    # D.6: mount certidoes BEFORE the catch-all `/{licitacao_id}` route to
    # avoid "certidoes" being parsed as an int (returns 422).
    app.include_router(
        certidoes_router,
        prefix="/api/v1/licitacoes/certidoes",
        tags=["licitacoes-certidoes"],
    )
    app.include_router(licitacoes_router, prefix="/api/v1/licitacoes", tags=["licitacoes"])
    app.include_router(ia_tools_router, prefix="/api/v1/ia", tags=["ia-tools"])
    app.include_router(fiscal_router, prefix="/api/v1/fiscal", tags=["fiscal"])
    app.include_router(
        observability_router,
        prefix="/api/v1/observability",
        tags=["observability"],
    )

    return app


app = create_app()
