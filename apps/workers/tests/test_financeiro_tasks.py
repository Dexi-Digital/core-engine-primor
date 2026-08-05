"""Smoke da task de alertas de contrato (Squad 5)."""

from __future__ import annotations

import pytest

from worker.main import celery_app


def test_task_registrada() -> None:
    # `include=[...]` do Celery e lazy (so importado no boot real do
    # worker) -- importa o modulo aqui para forcar o registro da task
    # antes de checar `celery_app.tasks`, igual ao teste abaixo.
    import worker.tasks.financeiro  # noqa: F401

    assert "worker.tasks.financeiro.dispatch_contrato_alerts" in celery_app.tasks


def test_beat_schedule_tem_contrato_alerts() -> None:
    entry = celery_app.conf.beat_schedule.get("contrato-alerts-daily")
    assert entry is not None
    assert entry["task"] == "worker.tasks.financeiro.dispatch_contrato_alerts"


def test_sem_env_retorna_erro_explicativo(monkeypatch) -> None:
    """Sem CONTRATOS_ALERT_EMAILS a task nao explode -- devolve dict de
    erro (mesmo contrato da task de certidoes).

    O pacote `app` (apps/api) so esta no path quando os dois pacotes
    sao instalados juntos (ex.: rodando com PYTHONPATH=../api a partir
    de apps/workers). No job "workers" isolado do CI (so `apps/workers`
    instalado) o import lazy de `app.*` falha e a task cai no guard
    de `# pragma: no cover` -- por isso o skip aqui em vez de assumir
    que `app` sempre esta disponivel.
    """
    pytest.importorskip("app", reason="apps/api nao esta no PYTHONPATH neste runner")
    monkeypatch.delenv("CONTRATOS_ALERT_EMAILS", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    from worker.tasks.financeiro import dispatch_contrato_alerts

    result = dispatch_contrato_alerts.run()
    assert "error" in result
    assert "CONTRATOS_ALERT_EMAILS" in str(result["error"])
