"""Tasks do Modulo B (Manutencao / Frota).

`consulta_detran` dispara a consulta Infosimples assincrona pra um
veiculo em determinada UF (SP/MG/GO). Retry exponencial em erros de
transporte; o service ja registra `status='erro'` em `frota_consultas_detran`,
entao mesmo se a task der up apos exaurir retries, nada e perdido --
o worker apenas marca a row como erro e segue.

`ocr_parte_diaria` continua stub (Modulo B.2 ainda nao implementado).
"""
from __future__ import annotations

import asyncio
import logging

from worker.main import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.tasks.manutencao.consulta_detran",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def consulta_detran(
    self, veiculo_id: int, uf: str, actor: str | None = None
) -> dict[str, object]:
    """Consulta Detran de um veiculo via Infosimples.

    Args:
        veiculo_id: PK em `frota_veiculos`.
        uf: SP, MG ou GO.
        actor: email do usuario HTTP que disparou a consulta. Propagado
            pro `audit_log` do service. `None` = dispatched sem contexto
            (fallback: `SYSTEM_WORKER`).

    Retorna `{consulta_id, status, source, n_multas}` ou `{error: ...}`
    se algo falhou antes de chegar no service. O service em si nao
    propaga excecao -- guarda erro em row 'erro' -- entao retentar
    so faz sentido para falhas de carregamento da API/storage/db.
    """
    return asyncio.run(_run_consulta_detran(veiculo_id, uf, actor))


async def _run_consulta_detran(
    veiculo_id: int, uf: str, actor: str | None
) -> dict[str, object]:
    try:
        from app.audit.actors import SYSTEM_WORKER
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.modules.manutencao_frota.service import (
            consultar_detran,
            get_infosimples_client,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    client = get_infosimples_client(settings)
    try:
        async with SessionLocal() as session:
            consulta = await consultar_detran(
                session,
                veiculo_id,
                uf,
                client=client,
                actor=actor or SYSTEM_WORKER,
            )
        payload = consulta.payload or {}
        return {
            "consulta_id": consulta.id,
            "status": consulta.status,
            "source": consulta.source,
            "n_multas": len(payload.get("multas") or []),
            "error_msg": consulta.error_msg,
        }
    finally:
        await client.aclose()


@celery_app.task(name="worker.tasks.manutencao.rpa_despachante")
def rpa_despachante(placa: str) -> dict[str, object]:
    # Stub legado -- ficou aqui antes do B.3 entrar via Infosimples.
    # Pode ser removido quando confirmarmos que ninguem mais chama.
    return {"stub": True, "placa": placa}


@celery_app.task(
    name="worker.tasks.manutencao.ocr_parte_diaria",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def ocr_parte_diaria(
    self, parte_id: int, actor: str | None = None
) -> dict[str, object]:
    """Roda OCR de uma parte diaria (Modulo B.2) via Document AI.

    Args:
        parte_id: PK em `partes_diarias`.
        actor: email do uploader HTTP. Propagado pro `audit_log` do
            service (`processar_ocr_parte_diaria`) para auditar QUEM
            disparou o OCR. `None` = dispatched sem contexto (retry
            agendado por dead-letter, por ex.) -> fallback `SYSTEM_WORKER`.

    Retorna `{parte_id, ocr_status, source, error_msg}` ou
    `{error: ...}` se carga do app falhou. O service nao propaga
    excecao do Document AI -- guarda como `ocr_status='erro'`.
    Retentamos so em falhas de transporte/timeout (autoretry_for).
    """
    return asyncio.run(_run_ocr_parte_diaria(parte_id, actor))


async def _run_ocr_parte_diaria(
    parte_id: int, actor: str | None
) -> dict[str, object]:
    try:
        from app.audit.actors import SYSTEM_WORKER
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.modules.manutencao_frota.service import (
            get_documentai_client,
            open_partes_diarias_storage,
            processar_ocr_parte_diaria,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    client = get_documentai_client(settings)
    # Worker reusa o helper que a API tambem usa
    # (`open_partes_diarias_storage`) -- assim ambos respeitam
    # `STORAGE_BACKEND`. Sem isso, API saving em OneDrive escrevia o
    # item_id no DB e o worker tentava `LocalStorage.read(item_id)` ->
    # FileNotFoundError. Bug apontado pelo Devin Review.
    try:
        async with open_partes_diarias_storage(settings) as storage:
            async with SessionLocal() as session:
                parte = await processar_ocr_parte_diaria(
                    session,
                    parte_id,
                    client=client,
                    storage=storage,
                    actor=actor or SYSTEM_WORKER,
                )
        return {
            "parte_id": parte.id,
            "ocr_status": parte.ocr_status,
            "source": parte.ocr_source,
            "error_msg": parte.ocr_error_msg,
        }
    finally:
        await client.aclose()
