"""API-level tests for Licitacoes endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.licitacoes.models import Licitacao


@pytest.mark.asyncio
async def test_status_endpoint(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/licitacoes/status")
    assert r.status_code == 200
    body = r.json()
    assert body["module"] == "licitacoes"
    assert body["implemented"] is True


@pytest.mark.asyncio
async def test_list_empty(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/licitacoes")
    assert r.status_code == 200
    body = r.json()
    assert body == {"total": 0, "page": 1, "page_size": 20, "data": []}


@pytest.mark.asyncio
async def test_list_and_get_detail(api_client: AsyncClient, db_session) -> None:
    lic = Licitacao(
        external_id="12345678-2024-1",
        source="pncp",
        numero_compra="2024-0001",
        ano_compra=2024,
        sequencial_compra=1,
        objeto_compra="Obra de pavimentacao",
        modalidade_nome="Pregao Eletronico",
        orgao_cnpj="12345678000100",
        orgao_razao_social="Prefeitura Teste",
        uf_sigla="SP",
        municipio_nome="Cidade",
    )
    db_session.add(lic)
    await db_session.commit()

    r = await api_client.get("/api/v1/licitacoes?uf=SP")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["external_id"] == "12345678-2024-1"

    r2 = await api_client.get(f"/api/v1/licitacoes/{lic.id}")
    assert r2.status_code == 200
    assert r2.json()["objeto_compra"] == "Obra de pavimentacao"

    r3 = await api_client.get("/api/v1/licitacoes/99999")
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_list_search_param(api_client: AsyncClient, db_session) -> None:
    db_session.add_all(
        [
            Licitacao(
                external_id="srch-1",
                source="pncp",
                objeto_compra="Pavimentacao de estradas rurais",
                uf_sigla="MG",
            ),
            Licitacao(
                external_id="srch-2",
                source="pncp",
                objeto_compra="Merenda escolar para rede municipal",
                uf_sigla="MG",
            ),
        ]
    )
    await db_session.commit()

    r = await api_client.get("/api/v1/licitacoes?search=estradas")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["external_id"] == "srch-1"


@pytest.mark.asyncio
async def test_list_filter_no_match(api_client: AsyncClient, db_session) -> None:
    db_session.add(
        Licitacao(
            external_id="12345678-2024-2",
            source="pncp",
            uf_sigla="SP",
            modalidade_nome="Pregao Eletronico",
        )
    )
    await db_session.commit()
    r = await api_client.get("/api/v1/licitacoes?uf=RJ")
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_backfill_pncp_rejeita_janela_maior_que_um_ano(
    api_client: AsyncClient, auth_headers
) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/ingest/pncp/backfill"
        "?data_inicial=2025-01-01&data_final=2026-01-04",
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_backfill_pncp_enfileira_no_worker(
    api_client: AsyncClient, auth_headers, monkeypatch
) -> None:
    from app.modules.manutencao_frota import service

    class Job:
        id = "pncp-backfill-test"

    class Dispatcher:
        def __init__(self):
            self.args = None

        def send_task(self, name, *, args, queue):
            self.args = (name, args, queue)
            return Job()

    dispatcher = Dispatcher()
    monkeypatch.setattr(service, "get_celery_dispatcher", lambda: dispatcher)
    resp = await api_client.post(
        "/api/v1/licitacoes/ingest/pncp/backfill"
        "?data_inicial=2026-04-01&data_final=2026-04-30&uf=mg",
        headers=auth_headers,
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"task_id": "pncp-backfill-test", "status": "PENDING"}
    assert dispatcher.args == (
        "worker.tasks.licitacoes.backfill_pncp",
        ["2026-04-01", "2026-04-30", "MG"],
        "licitacoes",
    )


@pytest.mark.asyncio
async def test_backfill_pncp_exige_autenticacao(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/ingest/pncp/backfill"
        "?data_inicial=2026-04-01&data_final=2026-04-30"
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_tarefa_de_ingestao(api_client: AsyncClient, auth_headers, monkeypatch) -> None:
    from app.modules.manutencao_frota import service

    class Job:
        state = "SUCCESS"
        result = {"total_fetched": 12}

        def successful(self):
            return True

        def failed(self):
            return False

    class Dispatcher:
        def AsyncResult(self, task_id):
            assert task_id == "task-123"
            return Job()

    monkeypatch.setattr(service, "get_celery_dispatcher", lambda: Dispatcher())
    resp = await api_client.get(
        "/api/v1/licitacoes/ingest/tasks/task-123", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "task_id": "task-123", "status": "SUCCESS", "result": {"total_fetched": 12}
    }
