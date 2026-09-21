"""Tasks do modulo Juridico -- pull do contencioso no EasyJur."""
from __future__ import annotations

import asyncio

from worker.main import celery_app


@celery_app.task(name="worker.tasks.juridico.pull_easyjur")
def pull_easyjur() -> dict[str, object]:
    """Espelha processos e andamentos do EasyJur. Somente leitura.

    UM login por execucao: a sessao serve todas as paginas e o export.
    O EasyJur bloqueia a conta em 5 senhas erradas seguidas, e o client
    se recusa a tentar perto do limite -- entao credencial errada aqui
    falha alto em vez de queimar tentativa toda madrugada.
    """
    return asyncio.run(_run())


async def _run() -> dict[str, object]:
    try:
        from app.core import models_registry as _models  # noqa: F401
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.easyjur.client import EasyjurClient
        from app.modules.juridico import service as svc
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    client = EasyjurClient(
        email=settings.easyjur_email, password=settings.easyjur_password
    )
    if client.is_mock:
        await client.aclose()
        # Levanta em vez de devolver {"error": ...}: retorno normal
        # apareceria VERDE no beat, e "nao rodou" com cara de "rodou" e
        # o defeito que este projeto ja pagou caro tres vezes.
        raise RuntimeError("EASYJUR_EMAIL/EASYJUR_PASSWORD nao configurados")

    try:
        async with SessionLocal() as db:
            r = await svc.sincronizar(db, client, source="beat")
    finally:
        await client.aclose()
    return {
        "processos": r.processos,
        "andamentos": r.andamentos,
        "total_declarado": r.total_declarado,
        "divergencia": r.divergencia,
    }
