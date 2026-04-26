"""Celery app entrypoint.

Queues:
    - `dp_sesmt`      : varredura OCR, onboarding sync.
    - `manutencao`    : RPA despachante, OCR manuscrito.
    - `financeiro`    : OCR/XML de NFs, conciliacao.
    - `licitacoes`    : scrapers B2G (Conlicitacao, PNCP).
    - `ia`            : reconhecimento facial, LLM compras WhatsApp.
"""
from __future__ import annotations

import logging
import os

import structlog
from celery import Celery
from celery.signals import (
    before_task_publish,
    task_postrun,
    task_prerun,
    worker_process_init,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "primor",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "worker.tasks.dp_sesmt",
        "worker.tasks.manutencao",
        "worker.tasks.financeiro",
        "worker.tasks.licitacoes",
        "worker.tasks.fiscal",
        "worker.tasks.ia",
    ],
)

celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "worker.tasks.dp_sesmt.*": {"queue": "dp_sesmt"},
        "worker.tasks.manutencao.*": {"queue": "manutencao"},
        "worker.tasks.financeiro.*": {"queue": "financeiro"},
        "worker.tasks.licitacoes.*": {"queue": "licitacoes"},
        "worker.tasks.fiscal.*": {"queue": "financeiro"},
        "worker.tasks.ia.*": {"queue": "ia"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="America/Sao_Paulo",
    enable_utc=True,
)

# Celery Beat schedule. Boletins 3x/dia (07h, 13h, 19h America/Sao_Paulo).
# Keep cron expressions here (not in code) so infra can tune cadence without
# redeploy. To run beat: `celery -A worker.main beat -l info`.
from celery.schedules import crontab  # noqa: E402

celery_app.conf.beat_schedule = {
    "boletins-morning": {
        "task": "worker.tasks.licitacoes.dispatch_boletins",
        "schedule": crontab(hour="7", minute="0"),
    },
    "boletins-midday": {
        "task": "worker.tasks.licitacoes.dispatch_boletins",
        "schedule": crontab(hour="13", minute="0"),
    },
    "boletins-evening": {
        "task": "worker.tasks.licitacoes.dispatch_boletins",
        "schedule": crontab(hour="19", minute="0"),
    },
    # D.6: alertas de vencimento de certidoes uma vez por dia (08h America/Sao_Paulo).
    # Idempotente: a tabela `certidoes_alertas_log` impede reenvio na mesma janela.
    "certidao-alerts-daily": {
        "task": "worker.tasks.licitacoes.dispatch_certidao_alerts",
        "schedule": crontab(hour="8", minute="0"),
    },
    # A.2: alertas de vencimento de ASO uma vez por dia (08h05 America/Sao_Paulo).
    # 5min apos certidoes para distribuir carga do SMTP do Resend.
    # Idempotente via `dp_aso_alertas_log` (uniq employee_id + janela).
    "aso-alerts-daily": {
        "task": "worker.tasks.dp_sesmt.dispatch_aso_alerts",
        "schedule": crontab(hour="8", minute="5"),
    },
}


# --- Observability: propagar correlation_id da API para tasks --------------
#
# Quando a API enfileira uma task dentro de um request HTTP, queremos
# que os logs do worker carreguem o mesmo `correlation_id` para que a
# chain inteira (request -> task -> sub-tasks) apareca correlacionada
# em Datadog/Loki/etc.
#
# Estrategia:
# 1. `before_task_publish` (rodado dentro do processo da API): pega o
#    `correlation_id` do contextvar do structlog (set pelo middleware
#    de HTTP) e injeta no `headers` da mensagem Celery.
# 2. `task_prerun` (rodado dentro do processo do worker): le o header
#    da task e bind no contextvar local. Todos os logs da task vao
#    ganhar `correlation_id` automaticamente.
# 3. `task_postrun`: limpa o contextvar para nao vazar entre tasks
#    no mesmo worker process.
#
# Sem isso, os logs do worker so teriam o `task_id` da Celery -- nao
# da pra amarrar facilmente com a request HTTP que disparou a task.
CORRELATION_HEADER_KEY = "correlation_id"


@worker_process_init.connect
def _configure_worker_logging(**_: object) -> None:
    """Configura structlog dentro de cada worker process do Celery.

    `worker_process_init` dispara apos o fork do prefork pool -- e
    aqui que precisamos chamar `configure_logging`, nao no nivel do
    modulo, porque o fork pode duplicar handlers stdlib ja inicializados
    no master.
    """
    try:
        from app.core.logging import configure_logging
    except ImportError:
        # API package nao disponivel em alguns ambientes de teste.
        logging.basicConfig(level=logging.INFO)
        return
    configure_logging()


@before_task_publish.connect
def _attach_correlation_id_to_task(
    sender: str | None = None,
    headers: dict[str, object] | None = None,
    **_: object,
) -> None:
    """Anexa correlation_id (do contextvar atual) ao header da task.

    Roda no processo que esta enfileirando -- tipicamente a API. Se
    nao houver `correlation_id` bound (ex.: chamada manual via REPL,
    Celery beat schedulando), nao mexe nos headers.
    """
    if headers is None:
        return
    bound = structlog.contextvars.get_contextvars()
    correlation_id = bound.get("correlation_id")
    if correlation_id:
        headers[CORRELATION_HEADER_KEY] = correlation_id


@task_prerun.connect
def _bind_correlation_id_for_task(
    task=None, **_: object  # noqa: ANN001 (Celery passes the Task instance)
) -> None:
    """Le o header da task e bind no contextvar local do worker."""
    request = getattr(task, "request", None)
    if request is None:
        return
    headers = getattr(request, "headers", None) or {}
    correlation_id = (
        headers.get(CORRELATION_HEADER_KEY) if isinstance(headers, dict) else None
    )
    if correlation_id:
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            celery_task=getattr(task, "name", None),
            celery_task_id=getattr(request, "id", None),
        )
    else:
        # Tasks disparadas por beat / manualmente nao tem correlation
        # da API. Bind so o id da task para algum nivel de tracing.
        structlog.contextvars.bind_contextvars(
            celery_task=getattr(task, "name", None),
            celery_task_id=getattr(request, "id", None),
        )


@task_postrun.connect
def _clear_correlation_id_after_task(**_: object) -> None:
    """Limpa o contextvar do worker apos cada task.

    Sem isso, o `correlation_id` do request anterior vazaria pra
    proxima task que rodar no mesmo worker process.
    """
    structlog.contextvars.clear_contextvars()
