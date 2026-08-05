"""Tasks do Modulo C (Financeiro / Contratos) - stubs."""
from __future__ import annotations

import asyncio
import os

from worker.main import celery_app


@celery_app.task(name="worker.tasks.financeiro.parse_nf_xml")
def parse_nf_xml(xml_key: str) -> dict[str, object]:
    return {"stub": True, "xml_key": xml_key}


@celery_app.task(name="worker.tasks.financeiro.conciliacao_bancaria")
def conciliacao_bancaria(arquivo_retorno_key: str) -> dict[str, object]:
    return {"stub": True, "arquivo_retorno_key": arquivo_retorno_key}


@celery_app.task(name="worker.tasks.financeiro.dispatch_contrato_alerts")
def dispatch_contrato_alerts(recipients: list[str] | None = None) -> dict[str, object]:
    """Alertas de vencimento de contratos (Squad 5, demanda #12).

    Roda 1x/dia via beat. Destinatarios vem de CONTRATOS_ALERT_EMAILS
    (CSV) -- mesmo desenho de CERTIDOES_ALERT_EMAILS; `recipients`
    explicito e para testes manuais.
    """
    return asyncio.run(_run_contrato_alerts(recipients))


async def _run_contrato_alerts(
    recipients: list[str] | None,
) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.resend.client import ResendClient
        from app.modules.financeiro_contratos.alerts import (
            dispatch_contrato_alerts as _dispatch,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    if not settings.resend_api_key:
        return {"error": "RESEND_API_KEY not configured; skipping contrato alerts"}

    if recipients is None:
        env_val = os.getenv("CONTRATOS_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        return {"error": "CONTRATOS_ALERT_EMAILS not configured; nothing to send"}

    async with SessionLocal() as db:
        resend = ResendClient(api_key=settings.resend_api_key)
        try:
            summary = await _dispatch(db, resend, recipients=recipients)
        finally:
            await resend.aclose()
    return {
        "total_contratos": summary.total_contratos,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }
