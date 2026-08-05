"""Tests da Squad 5 -- ciclo de contratos (demanda #12, sem assinatura)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO,
    TIPOS_CONTRATO,
    Contrato,
    ContratoAlertaLog,
)
from app.modules.financeiro_contratos.service import (
    compute_vencimento_status,
    create_contrato,
    delete_contrato,
    get_contrato,
    list_contratos,
    update_contrato,
)


@pytest.mark.asyncio
async def test_contrato_model_roundtrip(db_session: AsyncSession) -> None:
    row = Contrato(
        titulo="Locacao de escavadeira CAT 320",
        contraparte_nome="TratorMax Ltda",
        contraparte_documento="12345678000190",
        tipo="locacao",
        valor=Decimal("15000.00"),
        data_inicio=date(2026, 1, 1),
        data_fim=date(2026, 12, 31),
        status="vigente",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.id is not None
    assert row.valor == Decimal("15000.00")
    assert row.obra_id is None
    assert row.arquivo_path is None
    assert row.easyjur_ref is None
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_contrato_alerta_log_unique_janela(db_session: AsyncSession) -> None:
    contrato = Contrato(
        titulo="X",
        contraparte_nome="Y",
        tipo="fornecedor",
        data_inicio=date(2026, 1, 1),
        status="vigente",
    )
    db_session.add(contrato)
    await db_session.commit()
    db_session.add(
        ContratoAlertaLog(
            contrato_id=contrato.id, janela="30d", recipients=["a@b.com"]
        )
    )
    await db_session.commit()
    db_session.add(
        ContratoAlertaLog(
            contrato_id=contrato.id, janela="30d", recipients=["a@b.com"]
        )
    )
    with pytest.raises(IntegrityError):  # uq_contrato_alerta_janela
        await db_session.commit()
    await db_session.rollback()
    count = len(
        (await db_session.execute(select(ContratoAlertaLog.id))).scalars().all()
    )
    assert count == 1


def test_tipos_e_status_canonicos() -> None:
    assert {"cliente", "fornecedor", "locacao"} == set(dict(TIPOS_CONTRATO))
    assert {"rascunho", "vigente", "encerrado", "judicializado"} == set(
        dict(STATUS_CONTRATO)
    )


def test_compute_vencimento_status_buckets() -> None:
    today = date(2026, 8, 4)
    assert compute_vencimento_status(date(2026, 12, 1), today=today) == "vigente"
    assert compute_vencimento_status(date(2026, 8, 20), today=today) == "vencendo"
    assert compute_vencimento_status(date(2026, 8, 1), today=today) == "vencido"
    assert compute_vencimento_status(None, today=today) == "sem_validade"


@pytest.mark.asyncio
async def test_create_e_get_contrato_com_audit(db_session: AsyncSession) -> None:
    row = await create_contrato(
        db_session,
        titulo="Obra BR-040 lote 2",
        contraparte_nome="DER-MG",
        tipo="cliente",
        data_inicio=date(2026, 1, 10),
        data_fim=date(2027, 1, 10),
        valor=250000000,
        status="vigente",
        actor="teste@primor.com",
    )
    assert row.id is not None
    # valor coagido para Decimal (Numeric(20,2), nunca float/int-cru no ORM)
    assert isinstance(row.valor, Decimal)
    assert row.valor == Decimal("250000000")
    fetched = await get_contrato(db_session, row.id)
    assert fetched is not None and fetched.titulo == "Obra BR-040 lote 2"
    # audit_log gravado com actor real
    from app.audit.models import AuditLog

    logs = (
        (await db_session.execute(select(AuditLog).where(
            AuditLog.resource == "financeiro.contrato"
        ))).scalars().all()
    )
    assert any(
        log.action == "create" and log.actor == "teste@primor.com" for log in logs
    )


@pytest.mark.asyncio
async def test_create_contrato_tipo_invalido(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await create_contrato(
            db_session,
            titulo="X",
            contraparte_nome="Y",
            tipo="permuta",  # nao esta em TIPOS_CONTRATO_VALIDOS
            data_inicio=date(2026, 1, 1),
        )


@pytest.mark.asyncio
async def test_create_contrato_valor_invalido(db_session: AsyncSession) -> None:
    # `Decimal(str("abc"))` levantaria decimal.InvalidOperation (viraria 500
    # no router se nao interceptado) -- o service converte para ValueError,
    # mesma convencao 422 do tipo/status invalidos.
    with pytest.raises(ValueError):
        await create_contrato(
            db_session,
            titulo="X",
            contraparte_nome="Y",
            tipo="cliente",
            data_inicio=date(2026, 1, 1),
            valor="abc",
        )


@pytest.mark.asyncio
async def test_list_contratos_filtros(db_session: AsyncSession) -> None:
    today = date(2026, 8, 4)
    await create_contrato(
        db_session, titulo="A", contraparte_nome="F1", tipo="fornecedor",
        data_inicio=today, data_fim=today + timedelta(days=10), status="vigente",
    )
    await create_contrato(
        db_session, titulo="B", contraparte_nome="F2", tipo="locacao",
        data_inicio=today, data_fim=today + timedelta(days=90), status="vigente",
    )
    await create_contrato(
        db_session, titulo="C", contraparte_nome="F3", tipo="cliente",
        data_inicio=today, status="encerrado",
    )
    assert len(await list_contratos(db_session)) == 3
    assert [c.titulo for c in await list_contratos(db_session, tipo="locacao")] == ["B"]
    assert [c.titulo for c in await list_contratos(db_session, status="encerrado")] == ["C"]
    # vence_em_dias=30: somente A (10 dias); B vence em 90; C sem data_fim
    vencendo = await list_contratos(db_session, vence_em_dias=30, today=today)
    assert [c.titulo for c in vencendo] == ["A"]


@pytest.mark.asyncio
async def test_update_e_delete_contrato(db_session: AsyncSession) -> None:
    row = await create_contrato(
        db_session, titulo="Antigo", contraparte_nome="Z", tipo="cliente",
        data_inicio=date(2026, 1, 1),
    )
    updated = await update_contrato(
        db_session, row.id, titulo="Novo", status="judicializado",
        easyjur_ref="EJ-2026-0042", valor=1234.1, actor="teste@primor.com",
    )
    assert updated is not None
    assert updated.titulo == "Novo"
    assert updated.easyjur_ref == "EJ-2026-0042"
    # valor via update tambem passa por Decimal(str(...)) -- float 1234.1
    # deve virar Decimal("1234.1") exato, sem ruido binario.
    assert isinstance(updated.valor, Decimal)
    assert updated.valor == Decimal("1234.1")
    with pytest.raises(ValueError):
        await update_contrato(db_session, row.id, valor="abc")
    assert await update_contrato(db_session, 99999, titulo="x") is None
    assert await delete_contrato(db_session, row.id) is True
    assert await get_contrato(db_session, row.id) is None
    assert await delete_contrato(db_session, 99999) is False


@pytest.mark.asyncio
async def test_crud_contratos_via_api(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # create
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Locacao retroescavadeira",
            "contraparte_nome": "TratorMax",
            "contraparte_documento": "12345678000190",
            "tipo": "locacao",
            "valor": 900000,
            "data_inicio": "2026-08-01",
            "data_fim": "2026-08-20",
            "status": "vigente",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    contrato_id = body["id"]
    assert body["vencimento_status"] in ("vencendo", "vigente")
    assert body["dias_para_vencer"] is not None

    # list + filtro
    resp = await api_client.get("/api/v1/financeiro/contratos?tipo=locacao")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # get
    resp = await api_client.get(f"/api/v1/financeiro/contratos/{contrato_id}")
    assert resp.status_code == 200
    assert resp.json()["titulo"] == "Locacao retroescavadeira"

    # patch
    resp = await api_client.patch(
        f"/api/v1/financeiro/contratos/{contrato_id}",
        json={"status": "judicializado", "easyjur_ref": "EJ-77"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["easyjur_ref"] == "EJ-77"

    # delete
    resp = await api_client.delete(
        f"/api/v1/financeiro/contratos/{contrato_id}", headers=auth_headers
    )
    assert resp.status_code == 204
    resp = await api_client.get(f"/api/v1/financeiro/contratos/{contrato_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mutacoes_exigem_auth(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "X", "contraparte_nome": "Y", "tipo": "cliente",
            "data_inicio": "2026-01-01",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tipo_invalido_da_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "X", "contraparte_nome": "Y", "tipo": "permuta",
            "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_status_endpoint_implemented(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/financeiro/status")
    assert resp.status_code == 200
    assert resp.json()["implemented"] is True


@pytest.mark.asyncio
async def test_upload_arquivo_contrato(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forca LocalStorage numa raiz temporaria
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EDITAIS_STORAGE_PATH", str(tmp_path / "editais"))
    from app.core.config import get_settings

    get_settings.cache_clear()

    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Com anexo", "contraparte_nome": "Z", "tipo": "cliente",
            "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    contrato_id = resp.json()["id"]

    resp = await api_client.post(
        f"/api/v1/financeiro/contratos/{contrato_id}/arquivo",
        files={"arquivo": ("contrato.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["arquivo_path"] is not None
    assert "contratos" in body["arquivo_path"]

    # 404 para contrato inexistente
    resp = await api_client.post(
        "/api/v1/financeiro/contratos/99999/arquivo",
        files={"arquivo": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    get_settings.cache_clear()
