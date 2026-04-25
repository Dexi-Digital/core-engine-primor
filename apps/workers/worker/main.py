"""Celery app entrypoint.

Queues:
    - `dp_sesmt`      : varredura OCR, onboarding sync.
    - `manutencao`    : RPA despachante, OCR manuscrito.
    - `financeiro`    : OCR/XML de NFs, conciliacao.
    - `licitacoes`    : scrapers B2G (Conlicitacao, PNCP).
    - `ia`            : reconhecimento facial, LLM compras WhatsApp.
"""
from __future__ import annotations

import os

from celery import Celery

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
}
