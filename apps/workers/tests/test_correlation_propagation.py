"""Testes unitarios da propagacao de correlation_id no worker.

Os signals do Celery (`before_task_publish`, `task_prerun`,
`task_postrun`) sao acoplados ao processo do broker em prod, o que
torna o teste E2E caro. Aqui invocamos os handlers diretamente para
provar:

1. `before_task_publish` injeta o correlation_id atual no header da task
   quando ele esta bound no contextvar do structlog.
2. `task_prerun` le o header e bind no contextvar local do worker.
3. `task_postrun` limpa o contextvar para nao vazar entre tasks.
"""
from __future__ import annotations

from types import SimpleNamespace

import structlog

from worker.main import (
    CORRELATION_HEADER_KEY,
    _attach_correlation_id_to_task,
    _bind_correlation_id_for_task,
    _clear_correlation_id_after_task,
)


def test_before_publish_attaches_correlation_id() -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id="abc-123")
    headers: dict[str, object] = {}
    _attach_correlation_id_to_task(sender="worker.tasks.x", headers=headers)
    assert headers[CORRELATION_HEADER_KEY] == "abc-123"
    structlog.contextvars.clear_contextvars()


def test_before_publish_noop_when_no_correlation() -> None:
    structlog.contextvars.clear_contextvars()
    headers: dict[str, object] = {"existing": "keep"}
    _attach_correlation_id_to_task(sender="worker.tasks.x", headers=headers)
    assert CORRELATION_HEADER_KEY not in headers
    assert headers["existing"] == "keep"


def test_before_publish_handles_none_headers() -> None:
    """Defensive: alguns brokers passam headers=None pre-merge."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id="abc")
    # Nao deve crashar
    _attach_correlation_id_to_task(sender="x", headers=None)
    structlog.contextvars.clear_contextvars()


def test_task_prerun_binds_correlation_from_header() -> None:
    structlog.contextvars.clear_contextvars()
    fake_task = SimpleNamespace(
        name="worker.tasks.x.do_thing",
        request=SimpleNamespace(
            id="task-uuid",
            headers={CORRELATION_HEADER_KEY: "trace-xyz"},
        ),
    )
    _bind_correlation_id_for_task(task=fake_task)
    bound = structlog.contextvars.get_contextvars()
    assert bound["correlation_id"] == "trace-xyz"
    assert bound["celery_task"] == "worker.tasks.x.do_thing"
    assert bound["celery_task_id"] == "task-uuid"
    structlog.contextvars.clear_contextvars()


def test_task_prerun_binds_only_task_meta_when_no_header() -> None:
    """Tasks de Celery beat (cron) nao tem correlation -- ainda assim
    queremos `celery_task` bound para algum nivel de tracing."""
    structlog.contextvars.clear_contextvars()
    fake_task = SimpleNamespace(
        name="worker.tasks.cron.daily",
        request=SimpleNamespace(id="beat-1", headers={}),
    )
    _bind_correlation_id_for_task(task=fake_task)
    bound = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in bound
    assert bound["celery_task"] == "worker.tasks.cron.daily"
    structlog.contextvars.clear_contextvars()


def test_task_postrun_clears_contextvars() -> None:
    structlog.contextvars.bind_contextvars(
        correlation_id="abc",
        celery_task="t",
        celery_task_id="id",
    )
    _clear_correlation_id_after_task()
    assert structlog.contextvars.get_contextvars() == {}
