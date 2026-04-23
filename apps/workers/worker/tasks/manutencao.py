"""Tasks do Modulo B (Manutencao / Frota) - stubs."""
from __future__ import annotations

from worker.main import celery_app


@celery_app.task(name="worker.tasks.manutencao.rpa_despachante")
def rpa_despachante(placa: str) -> dict[str, object]:
    # TODO: Playwright headless para baixar IPVA/CRLV/multas.
    return {"stub": True, "placa": placa}


@celery_app.task(name="worker.tasks.manutencao.ocr_parte_diaria")
def ocr_parte_diaria(file_key: str) -> dict[str, object]:
    return {"stub": True, "file_key": file_key}
