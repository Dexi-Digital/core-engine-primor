"""Tasks do modulo Juridico -- pull do contencioso no EasyJur."""
from __future__ import annotations

import asyncio

from worker.main import celery_app


@celery_app.task(name="worker.tasks.juridico.pull_easyjur")
def pull_easyjur(source: str = "beat") -> dict[str, object]:
    """Espelha processos e andamentos do EasyJur. Somente leitura.

    `source="manual"` quando vem do botao "Sincronizar agora"; "beat"
    quando vem do agendador. O estado (em_andamento / ok / erro) fica no
    `juridico_sync_log` e e o que a tela mostra -- por isso QUALQUER
    falha e gravada la antes de ser relancada.

    UM login por execucao: a sessao serve todas as paginas e o export.
    O EasyJur bloqueia a conta em 5 senhas erradas seguidas, e o client
    se recusa a tentar perto do limite -- entao credencial errada aqui
    falha alto em vez de queimar tentativa toda madrugada.
    """
    return asyncio.run(_run(source))


async def _run(source: str) -> dict[str, object]:
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
    async with SessionLocal() as db:
        if client.is_mock:
            await client.aclose()
            msg = "EASYJUR_EMAIL/EASYJUR_PASSWORD nao configurados no worker"
            await svc.marcar_erro(db, source=source, mensagem=msg)
            # Levanta em vez de devolver {"error": ...}: retorno normal
            # apareceria VERDE no beat, e "nao rodou" com cara de "rodou"
            # e o defeito que este projeto ja pagou caro tres vezes.
            raise RuntimeError(msg)

        await svc.marcar_inicio(db, source=source)
        try:
            r = await svc.sincronizar(db, client, source=source)
        except Exception as exc:  # noqa: BLE001 -- o motivo vai para a tela
            await db.rollback()
            await svc.marcar_erro(
                db, source=source, mensagem=f"{type(exc).__name__}: {exc}"
            )
            raise
        finally:
            await client.aclose()
    return {
        "processos": r.processos,
        "andamentos": r.andamentos,
        "total_declarado": r.total_declarado,
        "divergencia": r.divergencia,
    }
