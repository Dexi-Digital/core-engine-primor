"""Structured logging setup com correlation_id propagado por contextvars.

A ideia: cada request HTTP recebe (ou gera) um `correlation_id` no
middleware (`app/core/middleware.py`) e o injeta em
`structlog.contextvars`. Todos os `logger.info(...)` no codigo apos isso
-- inclusive em servicos chamados pela request -- automaticamente
ganham o campo `correlation_id` no JSON sem ninguem precisar passar
adiante.

Quando uma task Celery e enfileirada, propagamos o `correlation_id` via
headers da mensagem; no worker, o `before_task_publish` /
`task_prerun` (ver `apps/workers/worker/main.py`) le o header e refaz o
bind no contextvar local da task. Assim os logs de OCR/Detran/email
ficam amarrados ao request original que disparou a task.
"""
from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(
    level: int | None = None, *, json_output: bool | None = None
) -> None:
    """Configura structlog.

    `level`: opcional; default `INFO` ou `LOG_LEVEL` da env.
    `json_output`: se `True` usa `JSONRenderer` (prod). Se `False`,
    `ConsoleRenderer` colorido (dev). Default le `LOG_FORMAT` da env
    (`json`/`console`); fallback: JSON quando `LOG_FORMAT=json` ou
    quando nao estiver em TTY (ex.: container/CI).
    """
    if level is None:
        env_level = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, env_level, logging.INFO)
    if json_output is None:
        log_format = os.getenv("LOG_FORMAT", "").lower()
        if log_format == "json":
            json_output = True
        elif log_format == "console":
            json_output = False
        else:
            # Default: console em TTY (dev), JSON em qualquer outra coisa
            # (containers em prod, CI).
            json_output = not sys.stderr.isatty()

    # `force=True` para nao deixar configuracao previa do uvicorn/celery
    # sobrescrita rolar; sem isso `basicConfig` vira no-op se algo ja
    # configurou o root logger.
    logging.basicConfig(level=level, format="%(message)s", force=True)
    renderer: structlog.types.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    structlog.configure(
        processors=[
            # Mescla qualquer chave bound via `bind_contextvars` (e.g.
            # `correlation_id`) na payload final do log.
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
