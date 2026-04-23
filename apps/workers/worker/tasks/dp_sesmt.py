"""Tasks do Modulo A (DP / SESMT) - stubs."""
from __future__ import annotations

from worker.main import celery_app


@celery_app.task(name="worker.tasks.dp_sesmt.ocr_varredura_onedrive")
def ocr_varredura_onedrive(folder_path: str) -> dict[str, object]:
    # TODO: integrar com OneDrive + Tesseract/Textract para detectar ASOs faltantes.
    return {"stub": True, "folder": folder_path}


@celery_app.task(name="worker.tasks.dp_sesmt.sync_onboarding")
def sync_onboarding(cpf: str) -> dict[str, object]:
    # TODO: disparar Dominio/Onvio/Tangerino/OnSafety em paralelo.
    return {"stub": True, "cpf": cpf}
