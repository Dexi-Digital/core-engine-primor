"""Testes do adapter Tangerino (ponto/Solides)."""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.integrations.tangerino.client import TangerinoClient


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
    r1 = await c.list_batidas(
        func["id"], start_date="2026-08-01", end_date="2026-08-05"
    )
    r2 = await c.list_batidas(
        func["id"], start_date="2026-08-01", end_date="2026-08-05"
    )
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
