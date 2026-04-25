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
        from app.modules.fiscal.router import (
            get_fiscal_storage,
        )
        from app.modules.fiscal.service import (
            enviar_para_dominio,
            get_dominio_client,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    client = get_dominio_client(settings)
    # `get_fiscal_storage` e um async generator; consumimos diretamente.
    # Importante: `get_fiscal_storage` (nao `get_editais_storage`) -- o
    # XML mora em `fiscal_storage_subdir`, nao na raiz dos editais. No
    # OneDrive isso muda o `root_folder` que o save/delete usa em paths
    # relativos.
    storage_gen = get_fiscal_storage()
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
