"""Tasks do modulo Juridico -- pull do contencioso no EasyJur."""
from __future__ import annotations

import asyncio
import logging
from time import monotonic

from worker.main import celery_app

logger = logging.getLogger(__name__)
_SYNC_TIMEOUT_S = 12 * 60
_SOFT_LIMIT_S = _SYNC_TIMEOUT_S + 60
_HARD_LIMIT_S = _SOFT_LIMIT_S + 60


@celery_app.task(
    name="worker.tasks.juridico.pull_easyjur",
    soft_time_limit=_SOFT_LIMIT_S,
    time_limit=_HARD_LIMIT_S,
)
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
    logger.info("easyjur.sync.started source=%s", source)
    started = monotonic()
    result = asyncio.run(_run(source))
    logger.info(
        "easyjur.sync.finished source=%s duration_s=%.1f",
        source,
        monotonic() - started,
    )
    return result


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

        try:
            async with asyncio.timeout(_SYNC_TIMEOUT_S):
                await svc.marcar_inicio(db, source=source)
                r = await svc.sincronizar(db, client, source=source)
        except TimeoutError as exc:
            await db.rollback()
            mensagem = (
                "Timeout: EasyJur nao concluiu processos e andamentos em "
                f"{_SYNC_TIMEOUT_S // 60} minutos"
            )
            await svc.marcar_erro(db, source=source, mensagem=mensagem)
            logger.error("easyjur.sync.timeout source=%s", source)
            raise TimeoutError(mensagem) from exc
        except Exception as exc:  # noqa: BLE001 -- o motivo vai para a tela
            await db.rollback()
            await svc.marcar_erro(
                db, source=source, mensagem=f"{type(exc).__name__}: {exc}"
            )
            logger.exception("easyjur.sync.failed source=%s", source)
            raise
        finally:
            await client.aclose()
    return {
        "processos": r.processos,
        "andamentos": r.andamentos,
        "total_declarado": r.total_declarado,
        "divergencia": r.divergencia,
    }
