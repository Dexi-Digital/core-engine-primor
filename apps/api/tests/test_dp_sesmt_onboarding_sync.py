"""Testes do push de onboarding OnSafety (etapa 3, ADR-001)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.audit.models import AuditLog
from app.integrations.onsafety.client import OnsafetyClient, OnsafetyError
from app.modules.dp_sesmt.router import get_onsafety_dep

# --- helpers ---------------------------------------------------------------

_CPF_VALIDO = "52998224725"


class FakeErrClient(OnsafetyClient):
    """Client real que sempre falha no push (simula upstream fora)."""

    def __init__(self) -> None:
        super().__init__(api_token="t-x")

    async def create_or_update_trabalhador(self, **kwargs):
        raise OnsafetyError("boom upstream")

    async def aclose(self) -> None:  # nao ha rede para fechar
        pass


async def _criar_employee(api_client: AsyncClient, auth_headers) -> int:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": _CPF_VALIDO,
            "nome_completo": "JOSE DA SILVA",
            "cargo": "Operador de Escavadeira",
            "data_admissao": "2026-07-01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _override(client: OnsafetyClient):
    from app.main import app

    app.dependency_overrides[get_onsafety_dep] = lambda: client
    return app


# --- POST /employees/{id}/sync-onsafety --------------------------------------


@pytest.mark.asyncio
async def test_sync_ok_com_mock_persiste_run(
    api_client: AsyncClient, auth_headers, db_session
):
    emp_id = await _criar_employee(api_client, auth_headers)
    app = _override(OnsafetyClient(api_token=None))  # mock
    try:
        resp = await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sistema"] == "onsafety"
    assert body["external_id"].startswith("mock-")
    assert body["source"] == "onsafety_mock"
    assert body["correlation_id"] == f"onsafety-emp-{emp_id}"

    # audit gravado com actor do usuario logado
    logs = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "dp_sesmt.onboarding_sync"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].action == "create"
    assert logs[0].actor == "test-admin@primor.com"


@pytest.mark.asyncio
async def test_sync_erro_upstream_vira_row_erro_201(
    api_client: AsyncClient, auth_headers, db_session
):
    emp_id = await _criar_employee(api_client, auth_headers)
    app = _override(FakeErrClient())
    try:
        resp = await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "erro"
    assert "boom upstream" in body["error_msg"]
    assert body["external_id"] is None

    logs = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "dp_sesmt.onboarding_sync"
                )
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[-1].action == "error"


@pytest.mark.asyncio
async def test_sync_reprocesso_mesmo_correlation_id(
    api_client: AsyncClient, auth_headers
):
    emp_id = await _criar_employee(api_client, auth_headers)
    app = _override(OnsafetyClient(api_token=None))
    try:
        r1 = await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety",
            headers=auth_headers,
        )
        r2 = await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    # Reprocesso: nova row (historico append-only), MESMO correlation_id
    # e MESMO external_id (upsert deterministico por codigoExterno).
    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["correlation_id"] == r2.json()["correlation_id"]
    assert r1.json()["external_id"] == r2.json()["external_id"]


@pytest.mark.asyncio
async def test_sync_404_employee_inexistente(
    api_client: AsyncClient, auth_headers
):
    resp = await api_client.post(
        "/api/v1/dp-sesmt/employees/99999/sync-onsafety",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sync_exige_auth(api_client: AsyncClient):
    resp = await api_client.post(
        "/api/v1/dp-sesmt/employees/1/sync-onsafety"
    )
    assert resp.status_code == 401


# --- GET /employees/{id}/sync-onsafety ---------------------------------------


@pytest.mark.asyncio
async def test_historico_ordenado_mais_recente_primeiro(
    api_client: AsyncClient, auth_headers
):
    emp_id = await _criar_employee(api_client, auth_headers)
    app = _override(OnsafetyClient(api_token=None))
    try:
        await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety",
            headers=auth_headers,
        )
        await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    resp = await api_client.get(
        f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety"
    )
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 2
    assert runs[0]["id"] > runs[1]["id"]


@pytest.mark.asyncio
async def test_historico_404_employee_inexistente(api_client: AsyncClient):
    resp = await api_client.get(
        "/api/v1/dp-sesmt/employees/99999/sync-onsafety"
    )
    assert resp.status_code == 404
