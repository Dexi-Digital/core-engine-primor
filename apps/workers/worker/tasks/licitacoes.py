"""Tasks do Modulo D (Licitacoes).

`crawler_pncp` roda a ingestion de `apps/api` aproveitando o mesmo modelo de
dados. Para isso, o worker precisa que `apps/api` esteja no PYTHONPATH
(Dockerfile ja monta ambos em `/app`).
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

from worker.main import celery_app


@celery_app.task(name="worker.tasks.licitacoes.crawler_pncp")
def crawler_pncp(
    data_inicial: str | None = None,
    data_final: str | None = None,
    uf: str | None = None,
    max_paginas: int | None = None,
) -> dict[str, object]:
    """Ingest PNCP publicacoes in a date window (default: yesterday)."""
    return asyncio.run(_run(data_inicial, data_final, uf, max_paginas))


async def _run(
    data_inicial: str | None,
    data_final: str | None,
    uf: str | None,
    max_paginas: int | None,
) -> dict[str, object]:
    # Lazy imports so Celery doesnt require the API dependency tree at import time.
    try:
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.service import ingest_publicacoes
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    today = date.today()
    final = date.fromisoformat(data_final) if data_final else today
    inicial = date.fromisoformat(data_inicial) if data_inicial else (final - timedelta(days=1))

    async with SessionLocal() as db:
        client = PncpClient(base_url=os.getenv("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta"))
        try:
            result = await ingest_publicacoes(
                db,
                client,
                data_inicial=inicial,
                data_final=final,
                uf=uf,
                max_paginas=max_paginas,
            )
        finally:
            await client.aclose()
        return result.model_dump()


@celery_app.task(name="worker.tasks.licitacoes.analise_saude_municipal")
def analise_saude_municipal(municipio_id: str) -> dict[str, object]:
    return {"stub": True, "municipio_id": municipio_id}


@celery_app.task(name="worker.tasks.licitacoes.dispatch_boletins")
def dispatch_boletins(saved_query_id: int | None = None) -> dict[str, object]:
    """Despacha boletins por email. Rodada por Celery beat 3x/dia.

    Se `saved_query_id` for informado, processa apenas essa query (util para
    reenvio manual). Caso contrario, itera todas as queries ativas.
    """
    return asyncio.run(_run_boletins(saved_query_id))


async def _run_boletins(saved_query_id: int | None) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.resend.client import ResendClient
        from app.modules.licitacoes.boletins import dispatch_boletins as dispatch_service
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    if not settings.resend_api_key:
        return {"error": "RESEND_API_KEY not configured; skipping boletins dispatch"}

    async with SessionLocal() as db:
        resend = ResendClient(api_key=settings.resend_api_key)
        try:
            summary = await dispatch_service(
                db,
                resend,
                saved_query_ids=[saved_query_id] if saved_query_id else None,
            )
        finally:
            await resend.aclose()
    return summary.model_dump()
