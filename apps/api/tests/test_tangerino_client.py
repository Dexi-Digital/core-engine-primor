"""Testes do adapter Tangerino (ponto/Solides)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.core.config import Settings
from app.integrations.tangerino.client import (
    TangerinoAuthError,
    TangerinoClient,
    TangerinoError,
)


def test_settings_defaults_tangerino_e_onvio():
    s = Settings(_env_file=None)
    assert s.tangerino_api_key is None
    assert s.tangerino_base_url == "https://employer.tangerino.com.br"
    assert s.onvio_client_id is None
    assert s.onvio_client_secret is None
    assert s.onvio_integration_key is None
    assert s.onvio_audience == "409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
    assert s.onvio_allow_send is False


# --- mock (sem token) ------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_funcionarios_deterministico():
    c1 = TangerinoClient(api_token=None)
    c2 = TangerinoClient(api_token="")
    r1 = await c1.list_funcionarios()
    r2 = await c2.list_funcionarios()
    assert r1 == r2
    assert r1["source"] == "tangerino_mock"
    assert r1["total"] == 3
    assert len(r1["items"]) == 3


@pytest.mark.asyncio
async def test_mock_funcionarios_cpf_valido_e_workplaces_de_duas_obras():
    from app.core.cpf import is_valid_cpf

    c = TangerinoClient(api_token=None)
    r = await c.list_funcionarios()
    workplaces = set()
    for item in r["items"]:
        assert is_valid_cpf(item["cpf"])
        assert item["workplaces"], "funcionario mock sem workplace"
        workplaces.add(item["workplaces"][0]["nome"])
    assert len(workplaces) == 2  # 2 obras distintas no dataset


@pytest.mark.asyncio
async def test_mock_batidas_por_funcionario_e_estavel():
    c = TangerinoClient(api_token=None)
    func = (await c.list_funcionarios())["items"][0]
    r1 = await c.list_batidas(func["id"], start_date="2026-08-01", end_date="2026-08-05")
    r2 = await c.list_batidas(func["id"], start_date="2026-08-01", end_date="2026-08-05")
    assert r1 == r2
    assert r1["source"] == "tangerino_mock"
    assert r1["total"] > 0
    b = r1["items"][0]
    assert b["employee_id"] == func["id"]
    assert b["fim_ts"] > b["inicio_ts"]
    assert b["segundos_trabalhados"] > 0


@pytest.mark.asyncio
async def test_mock_locais_trabalho():
    c = TangerinoClient(api_token=None)
    r = await c.list_locais_trabalho()
    assert r["total"] == 2
    nomes = {w["nome"] for w in r["items"]}
    assert nomes == {"OBRA BR-040 LOTE 3", "OBRA MG-050 RECAPEAMENTO"}


@pytest.mark.asyncio
async def test_mock_health_check_true():
    c = TangerinoClient(api_token=None)
    assert await c.health_check() is True


# --- real (httpx MockTransport) --------------------------------------------


def _real_tangerino(handler):
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport, base_url="https://employer.tangerino.com.br")
    return TangerinoClient(api_token="tok-123", client=http)


def _spring(content, total):
    return Response(200, json={"content": content, "totalElements": total})


@pytest.mark.asyncio
async def test_real_list_funcionarios_normaliza_e_auth_header():
    seen = {}

    def handler(request: Request) -> Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return _spring(
            [
                {
                    "id": 7,
                    "externalId": "E-7",
                    "name": "JOSE DA SILVA",
                    "cpf": "529.982.247-25",
                    "pis": "12345678901",
                    "admissionDate": "2024-02-01",
                    "fired": False,
                    "workplaceList": [{"id": 1, "externalId": "OB-1", "name": "OBRA X"}],
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_funcionarios(page=0, size=50)
    assert seen["auth"] == "tok-123"
    assert seen["path"] == "/employee/find-all"
    assert seen["params"]["pageNumber"] == "0"
    assert seen["params"]["pageSize"] == "50"
    item = r["items"][0]
    assert item["cpf"] == "52998224725"
    assert item["workplaces"] == [{"id": 1, "external_id": "OB-1", "nome": "OBRA X"}]
    assert r["total"] == 1
    assert r["source"] == "tangerino"


@pytest.mark.asyncio
async def test_real_list_batidas_path_e_periodo():
    seen = {}

    def handler(request: Request) -> Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return _spring(
            [
                {
                    "employeeId": 7,
                    "employeeExternalId": "E-7",
                    "dateWorked": 1754006400000,
                    "startDateTimestamp": 1754032800000,
                    "endDateTimestamp": 1754065200000,
                    "workedTimeInSeconds": 28800,
                    "status": "CLOSED",
                    "pis": "12345678901",
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_batidas(7, start_date="2026-08-01", end_date="2026-08-05")
    assert seen["path"] == "/external/api/v1/payssego/punches/7"
    assert seen["params"]["startDate"] == "2026-08-01"
    assert seen["params"]["endDate"] == "2026-08-05"
    b = r["items"][0]
    assert b["segundos_trabalhados"] == 28800
    assert b["inicio_ts"] == 1754032800000
    assert r["source"] == "tangerino"


@pytest.mark.asyncio
async def test_real_401_vira_auth_error():
    def handler(request: Request) -> Response:
        return Response(401, json={"error": "Unauthorized"})

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoAuthError):
        await c.list_funcionarios()


@pytest.mark.asyncio
async def test_real_500_vira_tangerino_error():
    def handler(request: Request) -> Response:
        return Response(500, text="boom")

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoError):
        await c.list_locais_trabalho()


@pytest.mark.asyncio
async def test_real_locais_trabalho_normaliza():
    def handler(request: Request) -> Response:
        assert request.url.path == "/workplace/find-all"
        return _spring(
            [
                {
                    "id": 1,
                    "externalId": "OB-1",
                    "name": "OBRA X",
                    "active": True,
                    "standard": False,
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_locais_trabalho()
    assert r["items"] == [
        {
            "id": 1,
            "external_id": "OB-1",
            "nome": "OBRA X",
            "ativo": True,
            "padrao": False,
        }
    ]
