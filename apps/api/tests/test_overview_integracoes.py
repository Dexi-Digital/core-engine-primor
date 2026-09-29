"""Painel de integracoes da home: "configurado" vem das credenciais.

Ate 29/09/2026 OneDrive, Document AI e Resend eram `configured: True`
fixo, o Onvio era "Aguardando credencial" fixo e o TOTVS aparecia como
Protheus (o ERP e RM). O painel afirmava o que nao sabia.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio

CREDENCIAIS = (
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CLIENT_SECRET",
    "MS_GRAPH_DRIVE_ID",
    "GOOGLE_DOCUMENTAI_CREDENTIALS_JSON",
    "GCP_PROJECT_ID",
    "DOCUMENTAI_PROCESSOR_ID",
    "RESEND_API_KEY",
    "ONVIO_CLIENT_ID",
    "ONVIO_CLIENT_SECRET",
    "ONVIO_INTEGRATION_KEY",
)


@pytest.fixture
def sem_credenciais(monkeypatch):
    for nome in CREDENCIAIS:
        monkeypatch.setenv(nome, "")
    monkeypatch.setenv("ONVIO_ALLOW_SEND", "false")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


async def _painel(api_client: AsyncClient, auth_headers: dict) -> dict[str, dict]:
    r = await api_client.get("/api/v1/overview/home", headers=auth_headers)
    assert r.status_code == 200
    return {i["key"]: i for i in r.json()["integrations"]}


async def test_sem_credencial_nada_aparece_configurado(
    api_client: AsyncClient, auth_headers: dict, sem_credenciais
) -> None:
    painel = await _painel(api_client, auth_headers)
    for key in ("onedrive", "documentai", "resend", "dominio"):
        assert painel[key]["configured"] is False, key
        assert painel[key]["status"] == "pending", key
    assert "Protheus" not in painel["totvs"]["descr"]


async def test_onvio_com_credencial_mas_envio_desligado(
    api_client: AsyncClient, auth_headers: dict, sem_credenciais
) -> None:
    for nome in ("ONVIO_CLIENT_ID", "ONVIO_CLIENT_SECRET", "ONVIO_INTEGRATION_KEY"):
        sem_credenciais.setenv(nome, "x")
    get_settings.cache_clear()
    onvio = (await _painel(api_client, auth_headers))["dominio"]
    assert onvio["configured"] is True
    assert onvio["status"] == "idle"
    assert "ONVIO_ALLOW_SEND" in onvio["last_event"]
