"""Testes E2E do router fiscal: upload + lista + envio Domínio."""
from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.main import app
from app.modules.fiscal.models import DocumentoFiscal
from app.modules.fiscal.router import get_dominio_dep
from tests.fixtures.fiscal.samples import (
    BAIXA_XML,
    CFE_XML,
    CTE_XML,
    NFCE_65_XML,
    NFE_44_XML,
)


def _upload_payload(xml_bytes: bytes, filename: str = "doc.xml") -> dict:
    return {"arquivo": (filename, io.BytesIO(xml_bytes), "application/xml")}


@pytest.mark.asyncio
async def test_upload_nfe_persiste_e_extrai_metadata(api_client: AsyncClient):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "nfe.xml"),
        params={"source": "manual"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["tipo"] == "nfe"
    assert data["chave_acesso"] == "35240414200166000187550010000123451000000001"
    assert data["emitente_cnpj"] == "14200166000187"
    assert data["valor_total"] == "15750.50"
    assert data["status_envio"] == "pendente"
    assert data["source"] == "manual"
    assert data["xml_path"]


@pytest.mark.asyncio
async def test_upload_xml_vazio_retorna_422(api_client: AsyncClient):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(b"", "vazio.xml"),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_xml_malformado_retorna_422(api_client: AsyncClient):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(b"<NFe<infNFe", "bad.xml"),
    )
    assert r.status_code == 422
    assert "malformado" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_duplicado_retorna_409_com_existing_id(
    api_client: AsyncClient,
):
    """Idempotencia: o mesmo XML nao deve gerar 2 linhas. A 2a tentativa
    deve falhar com 409 Conflict + id do existente para a UI poder
    deeplink no documento original."""
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML)
    )
    assert r1.status_code == 201
    existing_id = r1.json()["id"]

    r2 = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML)
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["existing_id"] == existing_id


@pytest.mark.asyncio
async def test_lista_filtra_por_tipo_e_status(api_client: AsyncClient):
    """3 docs de tipos diferentes; filtros por tipo e status_envio
    isolam corretamente."""
    for xml in (NFE_44_XML, CTE_XML, BAIXA_XML):
        await api_client.post(
            "/api/v1/fiscal/documentos", files=_upload_payload(xml)
        )

    r_all = await api_client.get("/api/v1/fiscal/documentos")
    assert r_all.status_code == 200
    assert len(r_all.json()) == 3

    r_cte = await api_client.get("/api/v1/fiscal/documentos?tipo=cte")
    assert r_cte.status_code == 200
    cte_items = r_cte.json()
    assert len(cte_items) == 1
    assert cte_items[0]["tipo"] == "cte"

    r_pendente = await api_client.get(
        "/api/v1/fiscal/documentos?status_envio=pendente"
    )
    assert len(r_pendente.json()) == 3

    r_enviado = await api_client.get(
        "/api/v1/fiscal/documentos?status_envio=enviado"
    )
    assert len(r_enviado.json()) == 0


@pytest.mark.asyncio
async def test_get_404_em_id_inexistente(api_client: AsyncClient):
    r = await api_client.get("/api/v1/fiscal/documentos/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_valida_status_invalido(api_client: AsyncClient):
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML)
    )
    doc_id = r.json()["id"]

    # status invalido -> 422 (Pydantic schema).
    r_bad = await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"status_envio": "fantasma"},
    )
    assert r_bad.status_code == 422

    # observacoes funcionam.
    r_ok = await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"observacoes": "verificado em 2024-04-25"},
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["observacoes"] == "verificado em 2024-04-25"


@pytest.mark.asyncio
async def test_delete_remove_documento_e_audit(
    api_client: AsyncClient, db_session: AsyncSession
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML)
    )
    doc_id = r.json()["id"]

    r_del = await api_client.delete(f"/api/v1/fiscal/documentos/{doc_id}")
    assert r_del.status_code == 204

    # AGENTS.md: toda mutacao deve estar em audit_log.
    audits = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.resource == "fiscal.documento")
        )
    ).all()
    actions = sorted(a.action for a in audits)
    assert actions == ["create", "delete"]


@pytest.mark.asyncio
async def test_enviar_dominio_com_mock_marca_enviado(
    api_client: AsyncClient, db_session: AsyncSession
):
    """E2E happy path: upload -> envia para Dominio (mock) -> status
    vira `enviado` + protocolo + audit_log com `enviar_ok`."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFCE_65_XML)
    )
    doc_id = r.json()["id"]

    r_send = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio"
    )
    assert r_send.status_code == 200, r_send.text
    body = r_send.json()
    assert body["status_envio"] == "enviado"
    assert body["protocolo_dominio"]
    assert body["protocolo_dominio"].startswith("MOCK-NFCE-")

    # Persistencia + audit
    doc = await db_session.get(DocumentoFiscal, doc_id)
    assert doc is not None
    assert doc.status_envio == "enviado"
    assert doc.sent_at is not None

    audits = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.resource == "fiscal.documento")
        )
    ).all()
    actions = sorted(a.action for a in audits)
    assert "create" in actions
    assert "enviar_ok" in actions


@pytest.mark.asyncio
async def test_enviar_dominio_idempotente(api_client: AsyncClient):
    """Re-clique no botao 'Enviar' nao deve duplicar envio. 2a chamada
    devolve o mesmo protocolo, sem bater na Domínio de novo."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(CTE_XML)
    )
    doc_id = r.json()["id"]

    r1 = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio"
    )
    proto1 = r1.json()["protocolo_dominio"]

    r2 = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio"
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status_envio"] == "enviado"
    assert body["protocolo_dominio"] == proto1


class _FailingDominioClient:
    """Client que sempre devolve DominioError -- simula timeout/erro
    de transporte sem precisar dropar o transport."""

    name = "dominio_failing"

    async def health_check(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None

    async def upload_xml(self, **kwargs):
        from app.integrations.dominio.client import DominioError

        raise DominioError("timeout simulado")


@pytest.mark.asyncio
async def test_enviar_dominio_em_erro_persiste_status_e_retry(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Quando o adapter levanta DominioError, o servico tem que:
      - Marcar status_envio='erro'
      - Salvar error_msg
      - Incrementar retry_count
      - Gravar audit_log action='enviar_erro'
      - NAO re-levantar a excecao para o endpoint (devolve 200 + erro)
    """
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(CFE_XML)
    )
    doc_id = r.json()["id"]

    async def _override():
        yield _FailingDominioClient()

    app.dependency_overrides[get_dominio_dep] = _override
    try:
        r_send = await api_client.post(
            f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio"
        )
    finally:
        app.dependency_overrides.pop(get_dominio_dep, None)
    assert r_send.status_code == 200
    body = r_send.json()
    assert body["status_envio"] == "erro"
    assert "timeout" in (body["error_msg"] or "").lower()

    doc = await db_session.get(DocumentoFiscal, doc_id)
    assert doc is not None
    assert doc.retry_count == 1

    audits_count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "enviar_erro",
            AuditLog.resource == "fiscal.documento",
        )
    )
    assert audits_count == 1
