"""Testes do adapter Onvio (Dominio/Thomson Reuters -- NF-e)."""
from __future__ import annotations

import pytest

from app.integrations.onvio.client import OnvioClient

XML = b"<?xml version='1.0'?><NFe><infNFe Id='NFe123'/></NFe>"


@pytest.mark.asyncio
async def test_mock_send_deterministico():
    c1 = OnvioClient()
    c2 = OnvioClient(client_id="", client_secret=None, integration_key=None)
    r1 = await c1.send_nfe_xml(filename="nf.xml", content=XML)
    r2 = await c2.send_nfe_xml(filename="nf.xml", content=XML)
    assert r1 == r2
    assert r1["source"] == "onvio_mock"
    assert r1["batch_id"].startswith("mock-")


@pytest.mark.asyncio
async def test_mock_send_distingue_conteudos():
    c = OnvioClient()
    r1 = await c.send_nfe_xml(filename="a.xml", content=XML)
    r2 = await c.send_nfe_xml(filename="a.xml", content=XML + b"<!-- x -->")
    assert r1["batch_id"] != r2["batch_id"]


@pytest.mark.asyncio
async def test_mock_send_distingue_conteudos_com_mesmo_1kb_inicial():
    # Hash deve considerar o conteudo INTEIRO, nao so o primeiro 1KB --
    # XMLs de NF-e reais compartilham cabecalhos e divergem depois.
    header = b"<?xml version='1.0'?><NFe>" + b"<!-- pad -->" * 100
    assert len(header) > 1024
    c = OnvioClient()
    r1 = await c.send_nfe_xml(filename="a.xml", content=header + b"<final>A</final>")
    r2 = await c.send_nfe_xml(filename="a.xml", content=header + b"<final>B</final>")
    assert r1["batch_id"] != r2["batch_id"]


@pytest.mark.asyncio
async def test_mock_status_sempre_armazenado():
    c = OnvioClient()
    r = await c.send_nfe_xml(filename="nf.xml", content=XML)
    st = await c.get_batch_status(r["batch_id"])
    assert st["stored"] is True
    assert st["message"] == "Arquivo armazenado na API"
    assert st["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_check_activation():
    c = OnvioClient()
    info = await c.check_activation()
    assert len(info["escritorio_cnpj"]) == 14
    assert len(info["cliente_cnpj"]) == 14
    assert info["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_ignora_guard_allow_send():
    # Guard so vale para envio REAL; mock sempre permitido.
    c = OnvioClient(allow_send=False)
    r = await c.send_nfe_xml(filename="nf.xml", content=XML)
    assert r["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_health_check_true():
    assert await OnvioClient().health_check() is True


@pytest.mark.asyncio
async def test_health_check_nao_mock_tolera_not_implemented():
    # Ate as Tasks 5-6, o caminho real levanta NotImplementedError; o
    # health_check deve tratar isso como indisponivel (False), nao propagar.
    c = OnvioClient(
        client_id="id", client_secret="secret", integration_key="key"
    )
    assert c.is_mock is False
    assert await c.health_check() is False
