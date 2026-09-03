"""Tasks do Modulo A (DP / SESMT).

Tasks scheduladas pelo Celery beat (cron) rodam sem contexto HTTP. Quando
precisarem de um `actor` para gravar em `audit_log`, use `SYSTEM_BEAT` de
`app.audit.actors` (vale "system:beat") -- diferencia mutacoes cron de
uma chamada manual via endpoint `/dispatch` (que ja propaga email do admin).

Hoje `dispatch_aso_alerts` e `dispatch_afastamento_alerts` so enviam email
(sem audit). Se um dia gravarem audit por cada alerta disparado, o valor
canonico do actor nesse path e `SYSTEM_BEAT`.
"""
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
    """Push do onboarding para a OnSafety (etapa 3, ADR-001).

    Dominio/Onvio/Tangerino continuam TODO (aguardando credenciais) --
    entram aqui como novos `sistema` em `dp_onboarding_syncs`. O guard
    de escrita em producao vive no adapter (ONSAFETY_ALLOW_PROD_WRITE);
    bloqueio vira row `status="erro"`, sem stacktrace.
    """
    return asyncio.run(_run_sync_onboarding(cpf))


async def _run_sync_onboarding(cpf: str) -> dict[str, object]:
    try:
        from app.audit.actors import SYSTEM_WORKER
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.onsafety.client import OnsafetyClient
        from app.modules.dp_sesmt import service
        from app.modules.dp_sesmt.onboarding import sync_employee_onsafety
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    async with SessionLocal() as db:
        employee = await service.get_employee_by_cpf(db, cpf)
        if employee is None:
            return {"error": f"employee com cpf {cpf!r} nao encontrado"}
        client = OnsafetyClient(
            api_token=settings.onsafety_token,
            base_url=settings.onsafety_base_url,
            allow_prod_write=settings.onsafety_allow_prod_write,
        )
        try:
            run = await sync_employee_onsafety(
                db,
                client,
                employee,
                actor=SYSTEM_WORKER,
                projeto_id=settings.onsafety_projeto_id,
            )
        finally:
            await client.aclose()
    return {
        "employee_id": run.employee_id,
        "sistema": run.sistema,
        "status": run.status,
        "external_id": run.external_id,
        "error": run.error_msg,
    }


@celery_app.task(name="worker.tasks.dp_sesmt.pull_onsafety")
def pull_onsafety() -> dict[str, object]:
    """Pull SST da OnSafety: ASOs -> dp_employees.aso_*, EPIs ->
    dp_employee_documents (Squad 2, ADR-001).

    Roda 1x/dia (Celery beat 07h30) -- ANTES dos alertas ASO das 08h05,
    para os alertas usarem o dado fresco da OnSafety. Erro de upstream
    nao explode a task: volta em `error` com o parcial commitado.
    """
    return asyncio.run(_run_pull_onsafety())


async def _run_pull_onsafety() -> dict[str, object]:
    try:
        from dataclasses import asdict

        from app.audit.actors import SYSTEM_BEAT
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.onsafety.client import OnsafetyClient
        from app.modules.dp_sesmt.onsafety_sync import (
            pull_onsafety as pull_svc,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    async with SessionLocal() as db:
        client = OnsafetyClient(
            api_token=settings.onsafety_token,
            base_url=settings.onsafety_base_url,
        )
        try:
            summary = await pull_svc(db, client, actor=SYSTEM_BEAT)
        finally:
            await client.aclose()
    return asdict(summary)


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


@celery_app.task(name="worker.tasks.dp_sesmt.pull_ponto")
def pull_ponto(
    desde: str | None = None, ate: str | None = None, source: str = "beat"
) -> dict[str, object]:
    """Pull do ponto eletronico (Solides/Tangerino) -- Modulos A e B.

    Roda as 02h30, na madrugada: sao 395 funcionarios e a API do Solides
    so devolve batidas POR funcionario, entao e um loop longo. Fora do
    bloco 08h00-08h15 (certidoes/ASO/afastamentos/contratos) e antes do
    pull do TOTVS as 03h00, para os dois nao disputarem o worker.

    Somente leitura: nunca cria funcionario no DP nem escreve no Solides.
    """
    return asyncio.run(_run_pull_ponto(desde, ate, source))


async def _run_pull_ponto(
    desde: str | None, ate: str | None, source: str
) -> dict[str, object]:
    try:
        from datetime import date, timedelta

        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.core.locks import single_flight
        from app.integrations.tangerino.client import TangerinoClient
        from app.modules.ponto.service import (
            ingest_batidas,
            ingest_funcionarios,
            ingest_locais_trabalho,
        )
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    fim = date.fromisoformat(ate) if ate else date.today()
    inicio = (
        date.fromisoformat(desde)
        if desde
        else fim - timedelta(days=settings.ponto_pull_dias)
    )

    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    try:
        async with single_flight(
            "ponto:pull", redis=redis, ttl_s=settings.ponto_pull_lock_ttl_s
        ) as adquiriu:
            if not adquiriu:
                return {"skipped": True, "reason": "pull ja em execucao"}

            client = TangerinoClient(api_token=settings.tangerino_api_key)
            try:
                async with SessionLocal() as db:
                    # Ordem importa: locais antes de funcionarios (o
                    # vinculo com obra vem dali) e funcionarios antes de
                    # batidas (o loop de batidas percorre os ativos).
                    locais = await ingest_locais_trabalho(db, client, source=source)
                    funcs = await ingest_funcionarios(db, client, source=source)
                    batidas = await ingest_batidas(
                        db, client, desde=inicio, ate=fim, source=source
                    )
            finally:
                await client.aclose()
    finally:
        await redis.aclose()

    return {
        "locais": {"lidos": locais.lidos, "sem_obra": locais.sem_vinculo},
        "funcionarios": {"lidos": funcs.lidos, "sem_cadastro_dp": funcs.sem_vinculo},
        "batidas": {"lidos": batidas.lidos, "gravados": batidas.gravados},
        "janela": batidas.janela,
    }
