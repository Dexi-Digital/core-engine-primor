"""Tasks do Modulo A (DP / SESMT)."""
from __future__ import annotations

import asyncio
import os

from worker.main import celery_app


@celery_app.task(name="worker.tasks.dp_sesmt.ocr_varredura_onedrive")
def ocr_varredura_onedrive(folder_path: str) -> dict[str, object]:
    # TODO: integrar com OneDrive + Tesseract/Textract para detectar ASOs faltantes.
    return {"stub": True, "folder": folder_path}


@celery_app.task(name="worker.tasks.dp_sesmt.sync_onboarding")
def sync_onboarding(cpf: str) -> dict[str, object]:
    # TODO: disparar Dominio/Onvio/Tangerino/OnSafety em paralelo.
    return {"stub": True, "cpf": cpf}


@celery_app.task(name="worker.tasks.dp_sesmt.dispatch_aso_alerts")
def dispatch_aso_alerts(
    recipients: list[str] | None = None,
) -> dict[str, object]:
    """Dispatch alertas de vencimento de ASO (A.2).

    Roda 1x/dia (Celery beat 08h05). Os destinatarios vem de uma env
    var `ASO_ALERT_EMAILS` (CSV) -- assim o RH pode mudar quem recebe
    sem deploy. Quando `recipients` e informado explicitamente, usa
    esse valor (util para testes manuais e para POST /enviar manual
    do router).
    """
    return asyncio.run(_run_aso_alerts(recipients))


async def _run_aso_alerts(
    recipients: list[str] | None,
) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.resend.client import ResendClient
        from app.modules.dp_sesmt.aso_alerts import dispatch_aso_alerts as svc
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    if not settings.resend_api_key:
        return {"error": "RESEND_API_KEY not configured; skipping aso alerts"}

    if recipients is None:
        env_val = os.getenv("ASO_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        return {"error": "ASO_ALERT_EMAILS not configured; nothing to send"}

    async with SessionLocal() as db:
        resend = ResendClient(api_key=settings.resend_api_key)
        try:
            summary = await svc(db, resend, recipients=recipients)
        finally:
            await resend.aclose()
    return {
        "total_employees": summary.total_employees,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }


@celery_app.task(name="worker.tasks.dp_sesmt.dispatch_afastamento_alerts")
def dispatch_afastamento_alerts(
    recipients: list[str] | None = None,
) -> dict[str, object]:
    """Dispatch alertas de DCB / pericia de afastamentos INSS (D4).

    Roda 1x/dia (Celery beat 08h10). Janelas:
    - DCB: 30/15/7/0 dias.
    - Pericia: 15/7/0 dias.

    Destinatarios em `INSS_ALERT_EMAILS` (CSV) -- mesma estrategia do
    A.2/D.6.
    """
    return asyncio.run(_run_afastamento_alerts(recipients))


async def _run_afastamento_alerts(
    recipients: list[str] | None,
) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.resend.client import ResendClient
        from app.modules.dp_sesmt.afastamentos import (
            dispatch_afastamento_alerts as svc,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    if not settings.resend_api_key:
        return {
            "error": "RESEND_API_KEY not configured; skipping inss alerts"
        }

    if recipients is None:
        env_val = os.getenv("INSS_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        return {
            "error": "INSS_ALERT_EMAILS not configured; nothing to send"
        }

    async with SessionLocal() as db:
        resend = ResendClient(api_key=settings.resend_api_key)
        try:
            summary = await svc(db, resend, recipients=recipients)
        finally:
            await resend.aclose()
    return {
        "total_afastamentos": summary.total_afastamentos,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }
