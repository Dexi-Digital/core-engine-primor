"""Tasks do Modulo D (Licitacoes).

`crawler_pncp` roda a ingestion de `apps/api` aproveitando o mesmo modelo de
dados. Para isso, o worker precisa que `apps/api` esteja no PYTHONPATH
(Dockerfile ja monta ambos em `/app`).
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from worker.main import celery_app


@celery_app.task(name="worker.tasks.licitacoes.crawler_pncp")
def crawler_pncp(
    data_inicial: str | None = None,
    data_final: str | None = None,
    uf: str | None = None,
    max_paginas: int | None = None,
) -> dict[str, object]:
    """Ingest PNCP publicacoes in a date window (default: yesterday)."""
    return asyncio.run(_run(data_inicial, data_final, uf, max_paginas))


async def _run(
    data_inicial: str | None,
    data_final: str | None,
    uf: str | None,
    max_paginas: int | None,
) -> dict[str, object]:
    # Lazy imports so Celery doesnt require the API dependency tree at import time.
    try:
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.service import ingest_publicacoes
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    today = date.today()
    final = date.fromisoformat(data_final) if data_final else today
    inicial = date.fromisoformat(data_inicial) if data_inicial else (final - timedelta(days=1))

    async with SessionLocal() as db:
        client = PncpClient(base_url=os.getenv("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta"))
        try:
            result = await ingest_publicacoes(
                db,
                client,
                data_inicial=inicial,
                data_final=final,
                uf=uf,
                max_paginas=max_paginas,
            )
        finally:
            await client.aclose()
        resumo = result.model_dump()
        _falhar_se_nada_rodou(resumo)
        return resumo


def _falhar_se_nada_rodou(resumo: dict[str, object]) -> None:
    """Crawler em que TUDO falhou nao pode terminar verde.

    Em 21/09/2026 o PNCP devolveu 503 nas 13 modalidades: a task voltou
    `total_fetched=0` e encerrou com sucesso. No beat isso e uma
    execucao saudavel sobre uma base que nao andou -- o mesmo defeito
    silencioso que deixou a tela parada em 22/04.

    So dispara quando nada veio E houve falha. Falha parcial segue
    valendo (uma modalidade fora do ar nao invalida as outras doze), e
    dia sem publicacao nenhuma e resultado legitimo.
    """
    falhas = resumo.get("failed_modalidades") or []
    if falhas and not resumo.get("total_fetched"):
        raise RuntimeError(
            f"crawler_pncp: nenhuma modalidade respondeu "
            f"({len(falhas)} falharam, 0 registros). PNCP fora do ar?"
        )


@celery_app.task(bind=True, name="worker.tasks.licitacoes.backfill_pncp")
def backfill_pncp(
    task: Any,
    data_inicial: str,
    data_final: str,
    uf: str | None = None,
) -> dict[str, object]:
    """Recarga historica do PNCP em janelas semanais, fora da API.

    Cada janela confirma seus upserts antes de seguir; se uma janela
    falhar, as demais ainda rodam e o resultado identifica o intervalo.
    Reexecutar o mesmo periodo e seguro (upsert por external_id).
    """
    inicio = date.fromisoformat(data_inicial)
    fim = date.fromisoformat(data_final)
    if inicio > fim:
        raise ValueError("data_inicial deve ser anterior ou igual a data_final")
    if (fim - inicio).days > 366:
        raise ValueError("recarga PNCP limitada a 367 dias por execucao")
    return asyncio.run(
        _run_backfill(
            inicio,
            fim,
            uf,
            report_progress=lambda progress: task.update_state(
                state="PROGRESS", meta=progress
            ),
        )
    )


async def _run_backfill(
    inicio: date,
    fim: date,
    uf: str | None,
    report_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    total_fetched = 0
    inserted = 0
    failed_windows: list[dict[str, str]] = []
    current = inicio
    total_windows = ((fim - inicio).days // 7) + 1
    windows_done = 0
    while current <= fim:
        chunk_end = min(current + timedelta(days=6), fim)
        try:
            result = await _run(
                current.isoformat(), chunk_end.isoformat(), uf, None
            )
        except Exception as exc:  # a janela fica identificada para retry
            failed_windows.append({
                "data_inicial": current.isoformat(),
                "data_final": chunk_end.isoformat(),
                "erro": f"{type(exc).__name__}: {exc}"[:500],
            })
        else:
            total_fetched += int(result.get("total_fetched", 0))
            inserted += int(result.get("inserted", 0))
        windows_done += 1
        if report_progress is not None:
            report_progress({
                "windows_done": windows_done,
                "total_windows": total_windows,
                "current_window_start": current.isoformat(),
                "current_window_end": chunk_end.isoformat(),
                "total_fetched": total_fetched,
                "inserted": inserted,
                "failed_windows": len(failed_windows),
            })
        current = chunk_end + timedelta(days=1)

    if failed_windows and total_fetched == 0:
        raise RuntimeError(
            f"recarga PNCP falhou em todas as janelas ({len(failed_windows)})"
        )
    return {
        "data_inicial": inicio.isoformat(),
        "data_final": fim.isoformat(),
        "uf": uf,
        "total_fetched": total_fetched,
        "inserted": inserted,
        "failed_windows": failed_windows,
    }


@celery_app.task(name="worker.tasks.licitacoes.ingest_resultados")
def ingest_resultados(
    dias: int = 30, uf: str | None = None, max_licitacoes: int = 200
) -> dict[str, object]:
    """Resultados homologados -- alimenta os dashboards comerciais.

    So existia como POST manual, e por isso `licitacoes_resultados`
    ficava vazia e a tela de Inteligencia comercial aparecia em branco,
    com cara de nao implementada.

    `max_licitacoes` sobe para 200 aqui: o default baixo (25) do
    endpoint existe pelo timeout do serverless, que nao se aplica ao
    worker.
    """
    return asyncio.run(_run_resultados(dias, uf, max_licitacoes))


async def _run_resultados(
    dias: int, uf: str | None, max_licitacoes: int
) -> dict[str, object]:
    try:
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.resultados import ingest_resultados as svc
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    async with SessionLocal() as db:
        client = PncpClient(
            base_url=os.getenv("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta")
        )
        try:
            r = await svc(db, client, dias=dias, uf=uf, max_licitacoes=max_licitacoes)
        finally:
            await client.aclose()
        return r.model_dump()


@celery_app.task(name="worker.tasks.licitacoes.ingest_atas")
def ingest_atas(
    data_inicial: str | None = None,
    data_final: str | None = None,
    max_paginas: int | None = 20,
) -> dict[str, object]:
    """Atas de registro de preco (base das adesoes).

    Janela padrao de 7 dias, casando com a cadencia semanal do beat --
    assim nenhuma semana fica sem cobertura.
    """
    return asyncio.run(_run_atas(data_inicial, data_final, max_paginas))


async def _run_atas(
    data_inicial: str | None, data_final: str | None, max_paginas: int | None
) -> dict[str, object]:
    try:
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.atas import ingest_atas as svc
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    final = date.fromisoformat(data_final) if data_final else date.today()
    inicial = (
        date.fromisoformat(data_inicial)
        if data_inicial
        else (final - timedelta(days=7))
    )

    async with SessionLocal() as db:
        client = PncpClient(
            base_url=os.getenv("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta")
        )
        try:
            r = await svc(
                db, client, data_inicial=inicial, data_final=final,
                max_paginas=max_paginas,
            )
        finally:
            await client.aclose()
        return r.model_dump()


@celery_app.task(name="worker.tasks.licitacoes.analise_saude_municipal")
def analise_saude_municipal(municipio_id: str) -> dict[str, object]:
    return {"stub": True, "municipio_id": municipio_id}


@celery_app.task(name="worker.tasks.licitacoes.dispatch_boletins")
def dispatch_boletins(saved_query_id: int | None = None) -> dict[str, object]:
    """Despacha boletins como notificacao na plataforma (3x/dia).

    Se `saved_query_id` for informado, processa apenas essa query (util para
    reenvio manual). Caso contrario, itera todas as queries ativas.
    """
    return asyncio.run(_run_boletins(saved_query_id))


async def _run_boletins(saved_query_id: int | None) -> dict[str, object]:
    try:
        from app.core import models_registry as _models  # noqa: F401
        from app.core.db import SessionLocal
        from app.modules.licitacoes.boletins import dispatch_boletins as dispatch_service
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    # Nao ha checagem de chave de e-mail: desde 21/09/2026 o boletim
    # e notificacao na plataforma. A checagem antiga fazia a task ABORTAR
    # em qualquer ambiente sem chave de email -- ou seja, o boletim nao
    # rodava e nada dizia isso alem de uma linha de log.
    async with SessionLocal() as db:
        summary = await dispatch_service(
            db,
            saved_query_ids=[saved_query_id] if saved_query_id else None,
        )
    return summary.model_dump()


@celery_app.task(name="worker.tasks.licitacoes.download_edital")
def download_edital(licitacao_id: int) -> dict[str, object]:
    """Baixa o edital + anexos (D.4) de uma licitacao via PNCP."""
    return asyncio.run(_run_download_edital(licitacao_id))


async def _run_download_edital(licitacao_id: int) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.editais import download_edital_for_licitacao
        from app.modules.licitacoes.storage import LocalStorage
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    storage = LocalStorage(get_settings().editais_storage_path)
    async with SessionLocal() as db:
        pncp = PncpClient()
        try:
            result = await download_edital_for_licitacao(
                db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
            )
        finally:
            await pncp.aclose()
    return result.model_dump()


@celery_app.task(name="worker.tasks.licitacoes.dispatch_certidao_alerts")
def dispatch_certidao_alerts(recipients: list[str] | None = None) -> dict[str, object]:
    """Dispatch alertas de vencimento de certidoes (D.6).

    Roda 1x/dia (Celery beat). Os destinatarios vem de uma env var
    `CERTIDOES_ALERT_EMAILS` (lista separada por virgula) -- assim a
    Primor pode mudar quem recebe sem deploy. Quando `recipients` e
    informado explicitamente, usa esse valor (util para testes manuais).
    """
    return asyncio.run(_run_certidao_alerts(recipients))


async def _run_certidao_alerts(
    recipients: list[str] | None,
) -> dict[str, object]:
    try:
        from app.core.db import SessionLocal
        from app.integrations.msgraph_mail.client import abrir_mail_client
        from app.modules.licitacoes.certidoes import dispatch_expiration_alerts
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    if recipients is None:
        env_val = os.getenv("CERTIDOES_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        return {"error": "CERTIDOES_ALERT_EMAILS not configured; nothing to send"}

    # Notificacao na plataforma sempre; e-mail (Microsoft 365) so quando
    # configurado -- a falta de e-mail nao pula mais o alerta.
    async with SessionLocal() as db, abrir_mail_client() as mailer:
        summary = await dispatch_expiration_alerts(
            db, recipients=recipients, mailer=mailer
        )
    return {
        "total_certidoes": summary.total_certidoes,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }


@celery_app.task(name="worker.tasks.licitacoes.processar_edital_aprovado")
def processar_edital_aprovado(licitacao_id: int) -> dict[str, object]:
    """Processa um edital aprovado na triagem (Captador Squad 2).

    Despachada pela Tela de Captacao (Squad 1) via `send_task` quando a
    analista aprova. Baixa anexos, cria pasta do projeto e identifica a
    planilha orcamentaria. Idempotente -- pode ser re-executada.
    """
    return asyncio.run(_run_processamento(licitacao_id))


async def _run_processamento(licitacao_id: int) -> dict[str, object]:
    try:
        from app.core.config import get_settings
        from app.core.db import SessionLocal
        from app.integrations.pncp.client import PncpClient
        from app.modules.licitacoes.processamento import processar_aprovado
        from app.modules.licitacoes.storage_factory import editais_storage
    except ImportError as exc:  # pragma: no cover
        return {"error": f"API package not available in worker: {exc}"}

    settings = get_settings()
    async with SessionLocal() as db:
        pncp = PncpClient(
            base_url=os.getenv(
                "PNCP_BASE_URL", "https://pncp.gov.br/api/consulta"
            )
        )
        try:
            async with editais_storage(settings) as storage:
                result = await processar_aprovado(
                    db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
                )
        finally:
            await pncp.aclose()
    return result.model_dump()
