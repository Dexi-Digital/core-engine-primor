"""Testes do adapter Infosimples (Modulo B.3)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.infosimples.client import (
    UFS_SUPORTADAS,
    InfosimplesClient,
    InfosimplesError,
    InfosimplesUFNaoSuportadaError,
    normalize_placa,
)

# --- helpers ---------------------------------------------------------------


def _real_client(handler):
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport, base_url="https://api.infosimples.com")
    return InfosimplesClient(api_token="t-real", client=http)


# --- normalize_placa --------------------------------------------------------


def test_normalize_placa_remove_separadores():
    assert normalize_placa("ABC-1234") == "ABC1234"
    assert normalize_placa("abc 1A23") == "ABC1A23"
    assert normalize_placa("") == ""


# --- mock client (sem token) -----------------------------------------------


@pytest.mark.asyncio
async def test_mock_client_e_deterministico():
    c1 = InfosimplesClient(api_token=None)
    c2 = InfosimplesClient(api_token="")
    r1 = await c1.consultar_veiculo("ABC1234", "SP")
    r2 = await c2.consultar_veiculo("ABC1234", "SP")
    assert r1["source"] == "infosimples_mock"
    assert r1 == r2  # mesma placa+UF -> mesma resposta
    assert r1["placa"] == "ABC1234"
    assert r1["uf"] == "SP"


@pytest.mark.asyncio
async def test_mock_client_diverge_por_uf():
    c = InfosimplesClient(api_token=None)
    sp = await c.consultar_veiculo("ABC1234", "SP")
    mg = await c.consultar_veiculo("ABC1234", "MG")
    assert sp["uf"] == "SP" and mg["uf"] == "MG"
    # `marca_modelo` ou `cor` ou `n_multas` deve diferir entre estados
    diff = (
        sp.get("marca_modelo") != mg.get("marca_modelo")
        or sp.get("cor") != mg.get("cor")
        or len(sp.get("multas") or []) != len(mg.get("multas") or [])
    )
    assert diff, "mock deve produzir respostas distintas por UF"


@pytest.mark.asyncio
async def test_mock_client_ipva_e_licenciamento_presentes():
    c = InfosimplesClient(api_token=None)
    r = await c.consultar_veiculo("ABC1234", "SP")
    assert r["ipva"]["exercicio"] == 2025
    assert r["licenciamento"]["exercicio"] == 2025


# --- validacao de input -----------------------------------------------------


@pytest.mark.asyncio
async def test_uf_nao_suportada_levanta():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(InfosimplesUFNaoSuportadaError):
        await c.consultar_veiculo("ABC1234", "RJ")


@pytest.mark.asyncio
async def test_placa_invalida_levanta_value_error():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(ValueError):
        await c.consultar_veiculo("ABC", "SP")


def test_ufs_suportadas_constante():
    assert frozenset({"SP", "MG", "GO"}) == UFS_SUPORTADAS


# --- real client (com MockTransport simulando Infosimples) -----------------


@pytest.mark.asyncio
async def test_real_client_normaliza_payload():
    captured: dict = {}

    def handler(req: Request) -> Response:
        captured["url"] = str(req.url)
        return Response(
            200,
            json={
                "code": 200,
                "code_message": "OK",
                "data": [
                    {
                        "renavam": "12345678900",
                        "chassi": "9BW123ABC456D7890",
                        "marca_modelo": "VW/CONSTELLATION",
                        "ano_modelo": 2021,
                        "cor": "BRANCA",
                        "combustivel": "DIESEL",
                        "situacao": "REGULAR",
                        "licenciamento": {
                            "exercicio": 2025,
                            "vencimento": "2025-09-30",
                            "pago": True,
                            "valor": "163.42",
                        },
                        "ipva": {
                            "exercicio": 2025,
                            "vencimento": "2025-04-30",
                            "pago": False,
                            "valor": "1234.56",
                        },
                        "multas": [
                            {
                                "auto": "AIT-X1",
                                "data": "2025-08-15",
                                "valor": "195.23",
                                "descricao": "Excesso de velocidade",
                            }
                        ],
                        "restricoes": ["ALIENACAO FIDUCIARIA"],
                    }
                ],
            },
        )

    c = _real_client(handler)
    try:
        r = await c.consultar_veiculo("ABC1234", "SP")
    finally:
        await c.aclose()
    assert "consultas/detran/sp/veiculo" in captured["url"]
    assert r["source"] == "infosimples"
    assert r["placa"] == "ABC1234"
    assert r["uf"] == "SP"
    assert r["renavam"] == "12345678900"
    assert r["situacao"] == "REGULAR"
    assert r["ipva"]["pago"] is False
    assert len(r["multas"]) == 1
    assert r["raw"] is not None  # mantemos original


@pytest.mark.asyncio
async def test_real_client_eleva_em_status_nao_2xx():
    def handler(req: Request) -> Response:
        return Response(503, text="upstream offline")

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="status 503"):
            await c.consultar_veiculo("ABC1234", "MG")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_real_client_eleva_em_code_nao_200():
    def handler(req: Request) -> Response:
        return Response(
            200,
            json={
                "code": 612,
                "code_message": "Site da Detran indisponivel",
                "data": [],
            },
        )

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="code=612"):
            await c.consultar_veiculo("ABC1234", "GO")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_real_client_eleva_em_data_vazia():
    def handler(req: Request) -> Response:
        return Response(200, json={"code": 200, "data": []})

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="sem dados"):
            await c.consultar_veiculo("ABC1234", "SP")
    finally:
        await c.aclose()
