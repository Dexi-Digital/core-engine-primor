"""Testes E2E do router fiscal: upload + lista + envio Domínio."""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.main import app
from app.modules.fiscal.models import DocumentoFiscal, DocumentoFiscalItem
from app.modules.fiscal.router import get_dominio_dep
from app.modules.fiscal.service import reset_dominio_singleton
from app.modules.obras.models import Obra
from tests.fixtures.fiscal.samples import (
    BAIXA_XML,
    CFE_XML,
    CTE_XML,
    NFCE_65_XML,
    NFE_44_XML,
    NFE_DETALHADA_XML,
)


@pytest.fixture(autouse=True)
def _reset_dominio_singleton_between_tests():
    """O DominioClient e singleton por-processo agora (para aproveitar
    token-cache em prod). Sem reset entre testes, o counter do mock
    contaminaria assertions de protocolo entre testes."""
    reset_dominio_singleton()
    yield
    reset_dominio_singleton()


def _upload_payload(xml_bytes: bytes, filename: str = "doc.xml") -> dict:
    return {"arquivo": (filename, io.BytesIO(xml_bytes), "application/xml")}


@pytest.mark.asyncio
async def test_upload_nfe_persiste_e_extrai_metadata(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "nfe.xml"),
        params={"source": "manual"},
        headers=auth_headers,
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
async def test_upload_xml_vazio_retorna_422(api_client: AsyncClient, auth_headers: dict[str, str]):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(b"", "vazio.xml"),
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_xml_malformado_retorna_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(b"<NFe<infNFe", "bad.xml"),
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "malformado" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_duplicado_retorna_409_com_existing_id(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
):
    """Idempotencia: o mesmo XML nao deve gerar 2 linhas. A 2a tentativa
    deve falhar com 409 Conflict + id do existente para a UI poder
    deeplink no documento original."""
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML), headers=auth_headers
    )
    assert r1.status_code == 201
    existing_id = r1.json()["id"]

    r2 = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML), headers=auth_headers
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["existing_id"] == existing_id


@pytest.mark.asyncio
async def test_lista_filtra_por_tipo_e_status(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    """3 docs de tipos diferentes; filtros por tipo e status_envio
    isolam corretamente."""
    for xml in (NFE_44_XML, CTE_XML, BAIXA_XML):
        await api_client.post(
            "/api/v1/fiscal/documentos", files=_upload_payload(xml), headers=auth_headers
        )

    r_all = await api_client.get("/api/v1/fiscal/documentos")
    assert r_all.status_code == 200
    assert len(r_all.json()) == 3

    r_cte = await api_client.get("/api/v1/fiscal/documentos?tipo=cte")
    assert r_cte.status_code == 200
    cte_items = r_cte.json()
    assert len(cte_items) == 1
    assert cte_items[0]["tipo"] == "cte"

    r_pendente = await api_client.get("/api/v1/fiscal/documentos?status_envio=pendente")
    assert len(r_pendente.json()) == 3

    r_enviado = await api_client.get("/api/v1/fiscal/documentos?status_envio=enviado")
    assert len(r_enviado.json()) == 0


@pytest.mark.asyncio
async def test_get_404_em_id_inexistente(api_client: AsyncClient):
    r = await api_client.get("/api/v1/fiscal/documentos/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_valida_status_invalido(api_client: AsyncClient, auth_headers: dict[str, str]):
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML), headers=auth_headers
    )
    doc_id = r.json()["id"]

    # status invalido -> 422 (Pydantic schema).
    r_bad = await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"status_envio": "fantasma"},
        headers=auth_headers,
    )
    assert r_bad.status_code == 422

    # observacoes funcionam.
    r_ok = await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"observacoes": "verificado em 2024-04-25"},
        headers=auth_headers,
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["observacoes"] == "verificado em 2024-04-25"


@pytest.mark.asyncio
async def test_delete_remove_documento_e_audit(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML), headers=auth_headers
    )
    doc_id = r.json()["id"]

    r_del = await api_client.delete(f"/api/v1/fiscal/documentos/{doc_id}", headers=auth_headers)
    assert r_del.status_code == 204

    # AGENTS.md: toda mutacao deve estar em audit_log.
    audits = (
        await db_session.scalars(select(AuditLog).where(AuditLog.resource == "fiscal.documento"))
    ).all()
    actions = sorted(a.action for a in audits)
    assert actions == ["create", "delete"]


@pytest.mark.asyncio
async def test_enviar_dominio_com_mock_marca_enviado(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    """E2E happy path: upload -> envia para Dominio (mock) -> status
    vira `enviado` + protocolo + audit_log com `enviar_ok`."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFCE_65_XML), headers=auth_headers
    )
    doc_id = r.json()["id"]

    r_send = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
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
        await db_session.scalars(select(AuditLog).where(AuditLog.resource == "fiscal.documento"))
    ).all()
    actions = sorted(a.action for a in audits)
    assert "create" in actions
    assert "enviar_ok" in actions


@pytest.mark.asyncio
async def test_enviar_dominio_idempotente(api_client: AsyncClient, auth_headers: dict[str, str]):
    """Re-clique no botao 'Enviar' nao deve duplicar envio. 2a chamada
    devolve o mesmo protocolo, sem bater na Domínio de novo."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(CTE_XML), headers=auth_headers
    )
    doc_id = r.json()["id"]

    r1 = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
    )
    proto1 = r1.json()["protocolo_dominio"]

    r2 = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
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
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    """Quando o adapter levanta DominioError, o servico tem que:
    - Marcar status_envio='erro'
    - Salvar error_msg
    - Incrementar retry_count
    - Gravar audit_log action='enviar_erro'
    - NAO re-levantar a excecao para o endpoint (devolve 200 + erro)
    """
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(CFE_XML), headers=auth_headers
    )
    doc_id = r.json()["id"]

    def _override():
        return _FailingDominioClient()

    app.dependency_overrides[get_dominio_dep] = _override
    try:
        r_send = await api_client.post(
            f"/api/v1/fiscal/documentos/{doc_id}/enviar-dominio", headers=auth_headers
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


@pytest.mark.asyncio
async def test_storage_path_inclui_xml_hash_para_evitar_colisao(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    # Regressao identificada em review #12: dois XMLs distintos com o mesmo nome
    # de arquivo ("doc.xml") cairiam no mesmo target do disco se o
    # storage usasse so (bucket, filename). O hash do XML precisa
    # entrar no path. Usamos NFE + CTE que tem hash garantidamente
    # diferente para forcar caminhos distintos -- e ambos sao
    # uploadados com o mesmo `filename="doc.xml"`.
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, filename="doc.xml"),
        headers=auth_headers,
    )
    r2 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(CTE_XML, filename="doc.xml"),
        headers=auth_headers,
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    doc1 = await db_session.get(DocumentoFiscal, r1.json()["id"])
    doc2 = await db_session.get(DocumentoFiscal, r2.json()["id"])
    assert doc1 is not None and doc2 is not None
    # Mesmo filename de upload, paths diferentes graças ao prefixo do
    # hash. Sem o fix, ambos terminariam em ".../doc.xml" -- o segundo
    # sobrescreveria o primeiro silenciosamente.
    assert doc1.xml_path != doc2.xml_path
    assert doc1.xml_hash[:16] in doc1.xml_path
    assert doc2.xml_hash[:16] in doc2.xml_path


@pytest.mark.asyncio
async def test_dominio_dep_devolve_singleton_entre_requests():
    # Regressao identificada em review #12: criar um DominioClient novo por
    # request invalida o token-cache (a Dominio rate-limita /token).
    # `get_dominio_dep` PRECISA devolver a mesma instancia entre
    # chamadas dentro do mesmo processo.
    from app.modules.fiscal.router import get_dominio_dep

    reset_dominio_singleton()
    c1 = get_dominio_dep()
    c2 = get_dominio_dep()
    assert c1 is c2, "get_dominio_dep deve cachear o client (token-cache)"


@pytest.mark.asyncio
async def test_fiscal_storage_isolado_do_editais_storage(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
):
    # Regressao identificada em review #12: fiscal_storage_subdir foi declarado
    # no config mas nunca era lido. Resultado: XMLs fiscais e PDFs
    # de edital iam para o mesmo `editais_storage_path/{licitacao_id}/`
    # com o `licitacao_id` do fiscal sendo um bucket numerico do hash --
    # podia colidir com IDs reais de licitacao.
    from app.core.config import get_settings

    settings = get_settings()
    r = await api_client.post(
        "/api/v1/fiscal/documentos", files=_upload_payload(NFE_44_XML), headers=auth_headers
    )
    assert r.status_code == 201
    doc = await db_session.get(DocumentoFiscal, r.json()["id"])
    assert doc is not None
    # O subdir fiscal precisa aparecer no path final; sem o fix o path
    # seria so `{editais_storage_path}/{bucket}/...`.
    assert settings.fiscal_storage_subdir in doc.xml_path


# ---------------------------------------------------------------------------
# Task 3 -- persistencia de detalhes NF-e (colunas novas + tabela de itens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modelo_persiste_detalhes_e_itens(db_session: AsyncSession):
    doc = DocumentoFiscal(
        tipo="nfe",
        chave_acesso="35240414200166000187550010000543211000000008",
        xml_path="fiscal/1/teste.xml",
        xml_hash="deadbeef" * 8,
        status_envio="pendente",
        retry_count=0,
        uf="SP",
        chave_dv_valida=True,
        valor_icms=Decimal("3000.00"),
        valor_ipi=Decimal("250.00"),
        valor_pis=Decimal("165.00"),
        valor_cofins=Decimal("760.00"),
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        DocumentoFiscalItem(
            documento_id=doc.id,
            ordem=1,
            codigo="CIM-CP2",
            descricao="Cimento CP-II 50kg",
            ncm="25232910",
            cfop="5102",
            unidade="SC",
            quantidade=Decimal("100.0000"),
            valor_unitario=Decimal("200.00"),
            valor_total=Decimal("20000.00"),
        )
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(DocumentoFiscalItem).where(
            DocumentoFiscalItem.documento_id == doc.id
        )
    )
    assert row is not None
    assert row.ncm == "25232910"
    assert doc.uf == "SP"
    assert doc.chave_dv_valida is True
    assert doc.obra_id is None


@pytest.mark.asyncio
async def test_upload_nfe_detalhada_persiste_impostos_e_itens(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_DETALHADA_XML, "nfe-detalhada.xml"),
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["uf"] == "SP"
    assert data["chave_dv_valida"] is True
    assert data["valor_icms"] == "3000.00"
    assert data["valor_cofins"] == "760.00"

    detail = await api_client.get(
        f"/api/v1/fiscal/documentos/{data['id']}", headers=auth_headers
    )
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["itens"]) == 2
    assert body["itens"][0]["ncm"] == "25232910"
    assert body["itens"][0]["cfop"] == "5102"
    assert body["itens"][1]["codigo"] == "ACO-CA50"


@pytest.mark.asyncio
async def test_upload_cte_nao_gera_itens_nem_detalhes(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    """Tipos != nfe/nfce seguem exatamente como antes (2o passe e no-op)."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(CTE_XML, "cte.xml"),
        headers=auth_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["uf"] is None
    assert data["valor_icms"] is None
    detail = await api_client.get(
        f"/api/v1/fiscal/documentos/{data['id']}", headers=auth_headers
    )
    assert detail.json()["itens"] == []


@pytest.mark.asyncio
async def test_filtros_periodo_valor_e_obra(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
):
    """NFE_44 (2024-04-15, 15750.50) e NFE_DETALHADA (2024-04-22, 25000.00):
    filtros de periodo, valor e obra isolam cada uma."""
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "a.xml"),
        headers=auth_headers,
    )
    r2 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_DETALHADA_XML, "b.xml"),
        headers=auth_headers,
    )
    assert r1.status_code == 201 and r2.status_code == 201
    id1, id2 = r1.json()["id"], r2.json()["id"]

    # Periodo: so a detalhada foi emitida a partir de 2024-04-20.
    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"emitida_de": "2024-04-20"},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert id2 in ids and id1 not in ids

    # Periodo com teto: so a NFE_44 ate 2024-04-18.
    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"emitida_ate": "2024-04-18"},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert id1 in ids and id2 not in ids

    # Valor minimo: so a detalhada passa de 20000.
    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"valor_min": "20000"},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert id2 in ids and id1 not in ids

    # Obra: cria uma obra, vincula so a detalhada, filtra por ela.
    obra = Obra(codigo="OB-100", nome="Rodovia Teste")
    db_session.add(obra)
    await db_session.commit()

    patch = await api_client.patch(
        f"/api/v1/fiscal/documentos/{id2}",
        json={"obra_id": obra.id},
        headers=auth_headers,
    )
    assert patch.status_code == 200
    assert patch.json()["obra_id"] == obra.id

    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"obra_id": obra.id},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert ids == [id2]


@pytest.mark.asyncio
async def test_patch_obra_inexistente_retorna_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "a.xml"),
        headers=auth_headers,
    )
    r = await api_client.patch(
        f"/api/v1/fiscal/documentos/{r1.json()['id']}",
        json={"obra_id": 999999},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "obra" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_patch_obra_null_desvincula(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
):
    obra = Obra(codigo="OB-101", nome="Ponte Teste")
    db_session.add(obra)
    await db_session.commit()
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "a.xml"),
        headers=auth_headers,
    )
    doc_id = r1.json()["id"]
    await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"obra_id": obra.id},
        headers=auth_headers,
    )
    r = await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"obra_id": None},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["obra_id"] is None
