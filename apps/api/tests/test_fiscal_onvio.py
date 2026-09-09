"""O módulo fiscal fala com o Onvio, não com o adapter legado.

Contexto: o ADR-003 (D1) define a API Onvio como o único canal de
máquina com o Domínio, e o `OnvioClient` implementa o fluxo real
(token Thomson Reuters -> activation -> invoice/v3/batches). Mas o
fiscal instanciava o `DominioClient`, que aponta para outro host com
outro fluxo de autenticação -- ou seja, o caminho de produção nunca
teria funcionado com as credenciais reais, que chegaram em 03/09/2026.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.fiscal.models import DocumentoFiscal
from app.modules.fiscal.router import get_dominio_dep

CFE_XML = b"<?xml version='1.0'?><CFe><infCFe Id='CFe987'/></CFe>"


def _upload_payload(xml: bytes) -> dict:
    return {"arquivo": ("doc.xml", xml, "application/xml")}


async def _cria_documento(api_client: AsyncClient, headers: dict[str, str]) -> int:
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(CFE_XML), headers=headers
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ------------------------------------------------------------- factory
def test_producao_nao_instancia_mais_o_adapter_legado():
    """Guarda: nenhum caminho de produção pode voltar a montar o
    `DominioClient`. Ele fala com `api.dominioexterior.com.br` e com um
    `POST /token` que não corresponde a nenhuma API documentada."""
    import inspect

    from app.modules.fiscal import service

    fonte = inspect.getsource(service)
    assert "DominioClient(" not in fonte, (
        "fiscal voltou a instanciar o DominioClient"
    )


def test_factory_monta_onvio_quando_ha_credencial(monkeypatch):
    from app.core.config import get_settings
    from app.modules.fiscal.service import get_dominio_client

    monkeypatch.setenv("ONVIO_CLIENT_ID", "cid")
    monkeypatch.setenv("ONVIO_CLIENT_SECRET", "csec")
    monkeypatch.setenv("ONVIO_INTEGRATION_KEY", "chave")
    get_settings.cache_clear()
    try:
        client = get_dominio_client(get_settings())
        assert type(client).__name__ == "OnvioClient"
        assert client.is_mock is False
    finally:
        get_settings.cache_clear()


def test_factory_cai_no_mock_sem_credencial(monkeypatch):
    from app.core.config import get_settings
    from app.modules.fiscal.service import get_dominio_client

    monkeypatch.setenv("ONVIO_CLIENT_ID", "")
    monkeypatch.setenv("ONVIO_CLIENT_SECRET", "")
    monkeypatch.setenv("ONVIO_INTEGRATION_KEY", "")
    get_settings.cache_clear()
    try:
        assert get_dominio_client(get_settings()).is_mock is True
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------- envio
class _OnvioOk:
    name = "onvio_fake"
    is_mock = False

    def __init__(self) -> None:
        self.chamadas: list[dict] = []

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def send_nfe_xml(self, *, filename: str, content: bytes) -> dict:
        self.chamadas.append({"filename": filename, "bytes": len(content)})
        return {"batch_id": "BATCH-123", "source": "onvio"}


@pytest.mark.asyncio
async def test_envio_ok_guarda_o_batch_id(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    """O Onvio devolve `batch_id`, não `protocolo`. A coluna
    `protocolo_dominio` passa a guardar esse identificador."""
    doc_id = await _cria_documento(api_client, auth_headers)
    fake = _OnvioOk()
    app.dependency_overrides[get_dominio_dep] = lambda: fake
    try:
        r = await api_client.post(
            f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_dominio_dep, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status_envio"] == "enviado"
    assert body["protocolo_dominio"] == "BATCH-123"
    assert len(fake.chamadas) == 1
    # o adapter Onvio nao recebe `tipo` -- o conteudo do XML define
    assert set(fake.chamadas[0]) == {"filename", "bytes"}


class _OnvioBloqueado:
    name = "onvio_bloqueado"
    is_mock = False

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def send_nfe_xml(self, *, filename: str, content: bytes) -> dict:
        from app.integrations.onvio.client import OnvioSendBlockedError

        raise OnvioSendBlockedError("ONVIO_ALLOW_SEND desligado")


@pytest.mark.asyncio
async def test_envio_bloqueado_nao_conta_como_erro(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    """`ONVIO_ALLOW_SEND=false` é um guard de configuração, não falha
    de envio. Marcar 'erro' e incrementar `retry_count` faria o worker
    reprocessar para sempre algo que nunca vai passar, e sujaria a
    contagem de falhas reais."""
    doc_id = await _cria_documento(api_client, auth_headers)
    app.dependency_overrides[get_dominio_dep] = lambda: _OnvioBloqueado()
    try:
        r = await api_client.post(
            f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_dominio_dep, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status_envio"] == "bloqueado"

    doc = await db_session.scalar(
        select(DocumentoFiscal).where(DocumentoFiscal.id == doc_id)
    )
    assert doc is not None
    assert doc.retry_count == 0, "bloqueio nao pode entrar na fila de retry"
    assert "ONVIO_ALLOW_SEND" in (doc.error_msg or "")


class _OnvioAuthRuim:
    name = "onvio_auth"
    is_mock = False

    async def health_check(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None

    async def send_nfe_xml(self, *, filename: str, content: bytes) -> dict:
        from app.integrations.onvio.client import OnvioAuthError

        raise OnvioAuthError("credenciais rejeitadas (401)")


@pytest.mark.asyncio
async def test_erro_de_auth_marca_erro_e_incrementa_retry(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    doc_id = await _cria_documento(api_client, auth_headers)
    app.dependency_overrides[get_dominio_dep] = lambda: _OnvioAuthRuim()
    try:
        r = await api_client.post(
            f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_dominio_dep, None)

    assert r.json()["status_envio"] == "erro"
    doc = await db_session.scalar(
        select(DocumentoFiscal).where(DocumentoFiscal.id == doc_id)
    )
    assert doc is not None and doc.retry_count == 1
