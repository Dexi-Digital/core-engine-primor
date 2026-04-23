"""Tasks do Modulo D (Licitacoes) - stubs."""
from __future__ import annotations

from worker.main import celery_app


@celery_app.task(name="worker.tasks.licitacoes.crawler_pncp")
def crawler_pncp(uf: str, data_inicio: str) -> dict[str, object]:
    return {"stub": True, "uf": uf, "data_inicio": data_inicio}


@celery_app.task(name="worker.tasks.licitacoes.analise_saude_municipal")
def analise_saude_municipal(municipio_id: str) -> dict[str, object]:
    return {"stub": True, "municipio_id": municipio_id}
