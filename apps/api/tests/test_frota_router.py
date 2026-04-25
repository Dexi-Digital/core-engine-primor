"""Testes do router de frota (Modulo B.1)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.modules.manutencao_frota.models import DocumentoVeiculo, Veiculo


def _veiculo_payload(**overrides) -> dict:
    base = {
        "placa": "ABC1234",
        "renavam": "12345678900",
        "chassi": "9BWZZZ377VT004251",
        "marca": "VW",
        "modelo": "Constellation 24.280",
        "ano_fabricacao": 2020,
        "ano_modelo": 2021,
        "tipo": "caminhao",
        "obra": "Obra Norte",
        "km_atual": 45000,
    }
    base.update(overrides)
    return base


# --- Validacao de entrada ---------------------------------------------------


@pytest.mark.asyncio
async def test_create_veiculo_minimo(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "abc1234"},  # case insensitive + sem outros campos
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["placa"] == "ABC1234"
    assert body["status"] == "ativo"


@pytest.mark.asyncio
async def test_create_veiculo_com_documentos(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _veiculo_payload(
        documentos=[
            {
                "tipo": "crlv",
                "numero": "12345",
                "validade": "2026-12-31",
            },
            {
                "tipo": "ipva",
                "valor": "1234.56",
                "validade": "2026-04-30",
            },
        ]
    )
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos", json=payload
    )
    assert r.status_code == 201
    body = r.json()
    assert len(body["documentos"]) == 2
    tipos = {d["tipo"] for d in body["documentos"]}
    assert tipos == {"crlv", "ipva"}


@pytest.mark.asyncio
async def test_create_veiculo_placa_invalida(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABCD123"},  # 7 chars mas padrao errado
    )
    assert r.status_code == 422
    assert "placa invalida" in r.text


@pytest.mark.asyncio
async def test_create_veiculo_renavam_invalido(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABC1234", "renavam": "12345678901"},  # DV nao bate
    )
    assert r.status_code == 422
    assert "renavam invalido" in r.text


@pytest.mark.asyncio
async def test_create_veiculo_chassi_invalido(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABC1234", "chassi": "ABC123"},  # 6 chars
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_status_invalido(api_client: AsyncClient) -> None:
    """Regressao: igual ao bug do M\u00f3dulo A -- update parcial nao
    pode aceitar status arbitrario."""
    cr = await api_client.post(
        "/api/v1/manutencao-frota/veiculos", json=_veiculo_payload()
    )
    assert cr.status_code == 201
    veiculo_id = cr.json()["id"]
    r = await api_client.patch(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}",
        json={"status": "fantasma"},
    )
    assert r.status_code == 422
    # E o veiculo nao foi corrompido.
    g = await api_client.get(f"/api/v1/manutencao-frota/veiculos/{veiculo_id}")
    assert g.json()["status"] == "ativo"


# --- Listagem + filtros -----------------------------------------------------


@pytest.mark.asyncio
async def test_list_filtros(api_client: AsyncClient) -> None:
    # 3 veiculos: 2 ativos (1 obra A, 1 obra B), 1 manutencao.
    await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json=_veiculo_payload(placa="ABC1234", obra="A", chassi=None, renavam=None),
    )
    await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json=_veiculo_payload(placa="DEF5678", obra="B", chassi=None, renavam=None),
    )
    await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json=_veiculo_payload(
            placa="GHI9012",
            obra="A",
            status="manutencao",
            chassi=None,
            renavam=None,
        ),
    )

    r_all = await api_client.get("/api/v1/manutencao-frota/veiculos")
    assert r_all.json()["total"] == 3

    r_ativo = await api_client.get(
        "/api/v1/manutencao-frota/veiculos", params={"status": "ativo"}
    )
    assert r_ativo.json()["total"] == 2

    r_obra_a = await api_client.get(
        "/api/v1/manutencao-frota/veiculos", params={"obra": "A"}
    )
    assert r_obra_a.json()["total"] == 2

    r_combo = await api_client.get(
        "/api/v1/manutencao-frota/veiculos",
        params={"obra": "A", "status": "ativo"},
    )
    assert r_combo.json()["total"] == 1
    assert r_combo.json()["items"][0]["placa"] == "ABC1234"


@pytest.mark.asyncio
async def test_search_por_placa(api_client: AsyncClient) -> None:
    await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json=_veiculo_payload(
            placa="ABC1234", chassi=None, renavam=None
        ),
    )
    await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json=_veiculo_payload(
            placa="XYZ9876", chassi=None, renavam=None
        ),
    )
    r = await api_client.get(
        "/api/v1/manutencao-frota/veiculos", params={"search": "abc"}
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["placa"] == "ABC1234"


# --- Audit log (AGENTS.md) --------------------------------------------------


@pytest.mark.asyncio
async def test_mutations_gravam_audit_log(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """AGENTS.md: toda mutacao de recurso sensivel grava em audit_log.

    Veiculos rastreiam IPVA + seguro + (em B.3) multas, entao create
    + update + delete tem que virar exatamente 3 linhas no audit_log.
    """
    cr = await api_client.post(
        "/api/v1/manutencao-frota/veiculos", json=_veiculo_payload()
    )
    veiculo_id = cr.json()["id"]
    await api_client.patch(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}",
        json={"obra": "Obra Sul"},
    )
    await api_client.delete(f"/api/v1/manutencao-frota/veiculos/{veiculo_id}")

    count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource == "manutencao_frota.veiculo",
        )
    )
    assert count == 3
    actions = (
        await db_session.execute(
            select(AuditLog.action).where(
                AuditLog.resource == "manutencao_frota.veiculo",
                AuditLog.resource_id == str(veiculo_id),
            ).order_by(AuditLog.id)
        )
    ).scalars().all()
    assert list(actions) == ["create", "update", "delete"]


@pytest.mark.asyncio
async def test_update_no_op_nao_grava_audit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH sem mudancas reais NAO deve sujar o audit log -- senao
    polui o relatorio de quem mexeu no que."""
    cr = await api_client.post(
        "/api/v1/manutencao-frota/veiculos", json=_veiculo_payload()
    )
    veiculo_id = cr.json()["id"]
    await api_client.patch(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}",
        json={"obra": "Obra Norte"},  # mesmo valor do payload
    )
    update_count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource == "manutencao_frota.veiculo",
            AuditLog.resource_id == str(veiculo_id),
            AuditLog.action == "update",
        )
    )
    assert update_count == 0


# --- Documentos -------------------------------------------------------------


@pytest.mark.asyncio
async def test_documento_crud(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    cr = await api_client.post(
        "/api/v1/manutencao-frota/veiculos", json=_veiculo_payload()
    )
    veiculo_id = cr.json()["id"]
    add = await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}/documentos",
        json={"tipo": "crlv", "numero": "999", "validade": "2027-01-15"},
    )
    assert add.status_code == 201
    doc_id = add.json()["id"]

    upd = await api_client.patch(
        f"/api/v1/manutencao-frota/veiculos/documentos/{doc_id}",
        json={"numero": "1000"},
    )
    assert upd.status_code == 200
    assert upd.json()["numero"] == "1000"

    delr = await api_client.delete(
        f"/api/v1/manutencao-frota/veiculos/documentos/{doc_id}"
    )
    assert delr.status_code == 204

    # documento removido fisicamente
    remaining = await db_session.scalar(
        select(func.count(DocumentoVeiculo.id)).where(
            DocumentoVeiculo.veiculo_id == veiculo_id
        )
    )
    assert remaining == 0

    # 3 linhas de audit do recurso documento (create/update/delete)
    audit_count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource == "manutencao_frota.documento",
        )
    )
    assert audit_count == 3


@pytest.mark.asyncio
async def test_documento_tipo_invalido(api_client: AsyncClient) -> None:
    cr = await api_client.post(
        "/api/v1/manutencao-frota/veiculos", json=_veiculo_payload()
    )
    veiculo_id = cr.json()["id"]
    r = await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}/documentos",
        json={"tipo": "fantasma"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_veiculo_cascata_documentos(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    cr = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json=_veiculo_payload(
            documentos=[{"tipo": "crlv", "numero": "1"}, {"tipo": "ipva"}]
        ),
    )
    veiculo_id = cr.json()["id"]
    await api_client.delete(f"/api/v1/manutencao-frota/veiculos/{veiculo_id}")

    veiculos = await db_session.scalar(
        select(func.count(Veiculo.id))
    )
    assert veiculos == 0
    docs = await db_session.scalar(
        select(func.count(DocumentoVeiculo.id))
    )
    assert docs == 0


# --- 404s -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_404s(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/manutencao-frota/veiculos/9999")
    assert r.status_code == 404
    r2 = await api_client.patch(
        "/api/v1/manutencao-frota/veiculos/9999", json={"obra": "X"}
    )
    assert r2.status_code == 404
    r3 = await api_client.delete("/api/v1/manutencao-frota/veiculos/9999")
    assert r3.status_code == 404
    r4 = await api_client.post(
        "/api/v1/manutencao-frota/veiculos/9999/documentos",
        json={"tipo": "crlv"},
    )
    assert r4.status_code == 404
