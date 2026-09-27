"""Validação e exportação do legado 90 na fila financeira."""
import asyncio

from worker.main import celery_app


@celery_app.task(name="worker.tasks.financeiro_importacao.validar")
def validar(lote_id: int) -> dict:
    """Propaga falhas para o Celery; o serviço persiste o motivo no lote."""
    return asyncio.run(_run(lote_id))


async def _run(lote_id: int) -> dict:
    from app.core import models_registry as _models  # noqa: F401
    from app.core.config import get_settings
    from app.core.db import SessionLocal, engine
    from app.modules.financeiro_importacao.service import processar
    from app.modules.licitacoes.storage_factory import editais_storage

    # Tasks usam asyncio.run; conexões de um loop anterior não podem ser reutilizadas.
    await engine.dispose()
    try:
        async with SessionLocal() as db, editais_storage(get_settings()) as storage:
            await processar(db, storage, lote_id)
        return {"lote_id": lote_id}
    finally:
        await engine.dispose()
