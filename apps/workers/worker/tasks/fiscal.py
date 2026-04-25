"""Tasks do Modulo C (Fiscal).

`enviar_dominio` faz o upload assincrono de um documento fiscal ja
importado para a Domínio. Retentativas com backoff exponencial -- erros
de transporte (timeout/DNS) sao recuperaveis; erros de auth (401/403)
nao sao (vai precisar rotar credenciais), entao nao retentamos.
"""
from __future__ import annotations

import asyncio
import logging

from worker.main import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.tasks.fiscal.enviar_dominio",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={"max_retries": 5},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def enviar_dominio(self, documento_id: int) -> dict[str, object]:
    """Envia um documento fiscal (XML ja persistido) para a Dominio.

    Args:
        documento_id: PK em `fiscal_documentos`.

    Retorna `{status_envio, protocolo_dominio, error_msg}` -- nao
    propaga DominioError porque o servico ja registra o erro no DB e
    em audit_log; o worker so re-tenta para erros transitorios.
    """
    return asyncio.run(_run(documento_id))


async def _run(documento_id: int) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.modules.fiscal.service import (
            enviar_para_dominio,
            get_dominio_client,
        )
        from app.modules.licitacoes.router import (
            get_editais_storage,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    client = get_dominio_client(settings)
    # `get_editais_storage` e um async generator; consumimos diretamente.
    storage_gen = get_editais_storage()
    storage = await storage_gen.__anext__()
    try:
        async with SessionLocal() as session:
            doc = await enviar_para_dominio(
                session,
                documento_id,
                dominio_client=client,
                storage=storage,
            )
        return {
            "documento_id": doc.id,
            "status_envio": doc.status_envio,
            "protocolo_dominio": doc.protocolo_dominio,
            "error_msg": doc.error_msg,
            "retry_count": doc.retry_count,
        }
    finally:
        try:
            await storage_gen.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("falha ao fechar storage no worker")
        await client.aclose()
