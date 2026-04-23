"""Tasks do Modulo E (IA) - stubs."""
from __future__ import annotations

from worker.main import celery_app


@celery_app.task(name="worker.tasks.ia.face_match_tangerino")
def face_match_tangerino(funcionario_id: str, foto_obra_keys: list[str]) -> dict[str, object]:
    return {"stub": True, "funcionario_id": funcionario_id, "matches": []}


@celery_app.task(name="worker.tasks.ia.cotacao_whatsapp")
def cotacao_whatsapp(demanda_id: str) -> dict[str, object]:
    return {"stub": True, "demanda_id": demanda_id}
