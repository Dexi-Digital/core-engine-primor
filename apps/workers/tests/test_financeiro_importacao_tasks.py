"""A importação financeira precisa ser consumida pela fila já existente."""
from unittest.mock import AsyncMock

from worker.main import celery_app
from worker.tasks import financeiro_importacao


def test_rota_e_registro_da_task():
    assert "worker.tasks.financeiro_importacao" in celery_app.conf.include
    route = celery_app.amqp.router.route({}, "worker.tasks.financeiro_importacao.validar")
    assert route["queue"].name == "financeiro"


def test_task_despacha_lote_sem_engolir_falha(monkeypatch):
    import pytest

    run = AsyncMock(side_effect=ValueError("arquivo inválido"))
    monkeypatch.setattr(financeiro_importacao, "_run", run)
    with pytest.raises(ValueError, match="arquivo inválido"):
        financeiro_importacao.validar(12)
    run.assert_awaited_once_with(12)
