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
def consulta_detran(self, veiculo_id: int, uf: str) -> dict[str, object]:
    """Consulta Detran de um veiculo via Infosimples.

    Args:
        veiculo_id: PK em `frota_veiculos`.
        uf: SP, MG ou GO.

    Retorna `{consulta_id, status, source, n_multas}` ou `{error: ...}`
    se algo falhou antes de chegar no service. O service em si nao
    propaga excecao -- guarda erro em row 'erro' -- entao retentar
    so faz sentido para falhas de carregamento da API/storage/db.
    """
    return asyncio.run(_run_consulta_detran(veiculo_id, uf))


async def _run_consulta_detran(
    veiculo_id: int, uf: str
) -> dict[str, object]:
    try:
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
                session, veiculo_id, uf, client=client
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


@celery_app.task(name="worker.tasks.manutencao.ocr_parte_diaria")
def ocr_parte_diaria(file_key: str) -> dict[str, object]:
    # Stub -- Modulo B.2 ainda nao implementado.
    return {"stub": True, "file_key": file_key}
