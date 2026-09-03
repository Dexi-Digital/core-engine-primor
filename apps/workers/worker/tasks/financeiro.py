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


@celery_app.task(name="worker.tasks.financeiro.pull_totvs")
def pull_totvs(
    desde: str | None = None, ate: str | None = None, source: str = "beat"
) -> dict[str, object]:
    """Pull de lancamentos financeiros do TOTVS RM (Modulo C).

    Roda na MADRUGADA (03h00), fora do horario comercial e fora do
    bloco 08h00-08h15 que ja tem certidoes, ASO, afastamentos e
    contratos (decisao #3).

    Por que so aqui e nunca na API: a licenca de WebService do RM e
    consumida POR REQUISICAO e so liberada quando ela termina -- 3
    chamadas simultaneas = 3 licencas. A escala de consumo sobe ate a
    TOTVS Full e nao e nomeavel, entao um pull descuidado tira assento
    de usuario real do ERP. Dai o lock single-flight OBRIGATORIO: se
    outra execucao ja esta rodando, esta desiste.
    """
    return asyncio.run(_run_pull_totvs(desde, ate, source))


async def _run_pull_totvs(
    desde: str | None, ate: str | None, source: str
) -> dict[str, object]:
    try:
        from datetime import date, timedelta

        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.core.locks import single_flight
        from app.integrations.totvs.client import build_totvs_client
        from app.modules.financeiro_totvs.service import ingest_lancamentos
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    fim = date.fromisoformat(ate) if ate else date.today()
    inicio = (
        date.fromisoformat(desde)
        if desde
        else fim - timedelta(days=settings.totvs_pull_dias)
    )

    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    try:
        async with single_flight(
            "totvs:pull:lancamentos",
            redis=redis,
            ttl_s=settings.totvs_pull_lock_ttl_s,
        ) as adquiriu:
            if not adquiriu:
                return {"skipped": True, "reason": "pull ja em execucao"}

            esperadas = {
                int(c.strip())
                for c in (settings.totvs_coligadas_esperadas or "").split(",")
                if c.strip()
            }
            client = build_totvs_client()
            try:
                async with SessionLocal() as db:
                    resumo = await ingest_lancamentos(
                        db,
                        client,
                        desde=inicio,
                        ate=fim,
                        source=source,
                        coligadas_esperadas=esperadas or None,
                    )
            finally:
                await client.aclose()
    finally:
        await redis.aclose()

    return {
        "janela": resumo.janela,
        "source": resumo.source,
        "extractor": resumo.extractor,
        "lidos": resumo.lidos,
        "gravados": resumo.gravados,
    }
