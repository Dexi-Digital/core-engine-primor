"""Tasks do Modulo C (Financeiro / Contratos) - stubs."""
from __future__ import annotations

from worker.main import celery_app


@celery_app.task(name="worker.tasks.financeiro.parse_nf_xml")
def parse_nf_xml(xml_key: str) -> dict[str, object]:
    return {"stub": True, "xml_key": xml_key}


@celery_app.task(name="worker.tasks.financeiro.conciliacao_bancaria")
def conciliacao_bancaria(arquivo_retorno_key: str) -> dict[str, object]:
    return {"stub": True, "arquivo_retorno_key": arquivo_retorno_key}
