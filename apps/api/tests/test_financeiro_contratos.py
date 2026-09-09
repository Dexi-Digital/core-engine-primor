"""Tests da Squad 5 -- ciclo de contratos (demanda #12, sem assinatura)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.resend.client import ResendClient
from app.modules.financeiro_contratos.alerts import (
    dispatch_contrato_alerts,
    render_alerta_contrato_html,
)
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
            # Datas RELATIVAS a hoje: `vencimento_status` e calculado
            # contra `date.today()`, entao data fixa envelhece e o
            # contrato vira "vencido" com a passagem do tempo (foi o
            # que quebrou este teste no main). 10 dias cabem na janela
            # de 30 dias -> "vencendo", estavel em qualquer data.
            "data_inicio": (date.today() - timedelta(days=30)).isoformat(),
            "data_fim": (date.today() + timedelta(days=10)).isoformat(),
            "status": "vigente",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    contrato_id = body["id"]
    assert body["vencimento_status"] == "vencendo"
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


@pytest.mark.asyncio
async def test_delete_contrato_remove_arquivo_do_storage(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cobertura do wiring `storage=storage` no DELETE (Task 4 review).

    Upload de PDF -> delete do contrato -> o arquivo fisico some do
    storage (nao so a linha do DB).
    """
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EDITAIS_STORAGE_PATH", str(tmp_path / "editais"))
    from app.core.config import get_settings

    get_settings.cache_clear()

    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Com anexo para deletar", "contraparte_nome": "Z",
            "tipo": "cliente", "data_inicio": "2026-01-01",
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
    arquivo_path = Path(resp.json()["arquivo_path"])
    assert arquivo_path.exists()

    resp = await api_client.delete(
        f"/api/v1/financeiro/contratos/{contrato_id}", headers=auth_headers
    )
    assert resp.status_code == 204

    resp = await api_client.get(f"/api/v1/financeiro/contratos/{contrato_id}")
    assert resp.status_code == 404
    assert not arquivo_path.exists()

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_delete_contrato_arquivo_path_inexistente_nao_falha(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caso benigno: `arquivo_path` aponta para arquivo ja removido do
    disco (ex.: limpeza manual, migracao de storage) -- delete do
    contrato precisa ser best-effort e nao pode falhar por isso."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EDITAIS_STORAGE_PATH", str(tmp_path / "editais"))
    from app.core.config import get_settings

    get_settings.cache_clear()

    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Anexo fantasma", "contraparte_nome": "Z",
            "tipo": "cliente", "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    contrato_id = resp.json()["id"]

    # Aponta arquivo_path para um caminho que nunca existiu no disco,
    # sem passar pelo endpoint de upload.
    row = await db_session.get(Contrato, contrato_id)
    assert row is not None
    ghost_path = tmp_path / "editais" / "MotorCentral" / "contratos" / str(
        contrato_id
    ) / "fantasma.pdf"
    assert not ghost_path.exists()
    row.arquivo_path = str(ghost_path)
    await db_session.commit()

    resp = await api_client.delete(
        f"/api/v1/financeiro/contratos/{contrato_id}", headers=auth_headers
    )
    assert resp.status_code == 204

    resp = await api_client.get(f"/api/v1/financeiro/contratos/{contrato_id}")
    assert resp.status_code == 404

    get_settings.cache_clear()


# --- alertas de vencimento (Task 5) ---------------------------------------


def _fake_resend(sent: list[dict]) -> ResendClient:
    """ResendClient com transporte mockado (mesma tecnica de
    test_licitacoes_certidoes.py: `client=` no construtor, nao overwrite
    de `_client` apos instanciar)."""

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        sent.append(_json.loads(request.content))
        return httpx.Response(200, json={"id": f"msg_{len(sent)}"})

    return ResendClient(
        api_key="test-key",
        client=httpx.AsyncClient(
            base_url="https://api.resend.com",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test-key"},
        ),
    )


def test_render_alerta_contrato_html_essentials() -> None:
    c = Contrato(
        id=7,
        titulo="Locacao escavadeira",
        contraparte_nome="TratorMax",
        tipo="locacao",
        data_inicio=date(2026, 1, 1),
        data_fim=date(2026, 8, 15),
        status="vigente",
    )
    html = render_alerta_contrato_html(
        c, janela=15, public_base_url="https://motor.example", dias_restantes=11
    )
    assert "Locacao escavadeira" in html
    assert "TratorMax" in html
    assert "15/08/2026" in html
    assert "11 dia(s)" in html
    assert "https://motor.example/financeiro/contratos" in html


@pytest.mark.asyncio
async def test_dispatch_contrato_alerts_idempotente(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 8, 4)
    vigente = await create_contrato(
        db_session, titulo="Vence em 10d", contraparte_nome="A", tipo="fornecedor",
        data_inicio=today, data_fim=today + timedelta(days=10), status="vigente",
    )
    await create_contrato(  # encerrado -> skipped_status
        db_session, titulo="Encerrado", contraparte_nome="B", tipo="cliente",
        data_inicio=today, data_fim=today + timedelta(days=5), status="encerrado",
    )
    await create_contrato(  # sem data_fim -> skipped_no_data_fim
        db_session, titulo="Indeterminado", contraparte_nome="C", tipo="cliente",
        data_inicio=today, status="vigente",
    )

    sent: list[dict] = []
    resend = _fake_resend(sent)
    try:
        summary = await dispatch_contrato_alerts(
            db_session, resend, recipients=["fin@primor.com"], today=today,
            public_base_url="https://motor.example",
        )
    finally:
        await resend.aclose()
    assert summary.sent == 1
    assert summary.skipped == 2
    assert summary.failed == 0
    assert len(sent) == 1
    assert "Vence em 10d" in sent[0]["html"]

    # segunda rodada no mesmo dia: nada novo (janela 15d ja logada)
    sent2: list[dict] = []
    resend2 = _fake_resend(sent2)
    try:
        summary2 = await dispatch_contrato_alerts(
            db_session, resend2, recipients=["fin@primor.com"], today=today,
            public_base_url="https://motor.example",
        )
    finally:
        await resend2.aclose()
    assert summary2.sent == 0
    assert len(sent2) == 0
    # e o log existe para o contrato vigente
    logs = (
        (await db_session.execute(
            select(ContratoAlertaLog).where(
                ContratoAlertaLog.contrato_id == vigente.id
            )
        )).scalars().all()
    )
    assert len(logs) == 1 and logs[0].janela == "15d" and logs[0].status == "sent"


@pytest.mark.asyncio
async def test_dispatch_alerts_rota_estatica_resolve_antes_do_dinamico(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """ROUTE-ORDERING: `POST /contratos/dispatch-alerts` precisa resolver
    para o endpoint estatico, nao ser capturado como `{contrato_id}`
    (o que daria 422 de int-parsing em vez de rodar o dispatch)."""
    resp = await api_client.post(
        "/api/v1/financeiro/contratos/dispatch-alerts",
        json={"recipients": ["fin@primor.com"]},
        headers=auth_headers,
    )
    # Sem RESEND_API_KEY configurada no ambiente de teste -> 503 (nao 422).
    # 422 indicaria que a rota dinamica `/contratos/{contrato_id}` capturou
    # "dispatch-alerts" tentando converte-lo para int.
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_dispatch_alerts_recipients_email_invalido_da_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Review final: `recipients` agora eh `list[EmailStr]` (paridade
    D.6) -- email mal formado deve ser rejeitado antes de qualquer
    chamada ao Resend, independente de RESEND_API_KEY estar setada."""
    resp = await api_client.post(
        "/api/v1/financeiro/contratos/dispatch-alerts",
        json={"recipients": ["nao-e-um-email"]},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_dispatch_alerts_recipients_acima_do_cap_da_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Review final: cap de `max_length=20` em `recipients` (paridade D.6)."""
    resp = await api_client.post(
        "/api/v1/financeiro/contratos/dispatch-alerts",
        json={"recipients": [f"user{i}@primor.com" for i in range(21)]},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_upload_arquivo_contrato_rejeita_nao_pdf(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Review final: content-type != application/pdf -> 422 claro."""
    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Guard nao-pdf", "contraparte_nome": "Z",
            "tipo": "cliente", "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    contrato_id = resp.json()["id"]

    resp = await api_client.post(
        f"/api/v1/financeiro/contratos/{contrato_id}/arquivo",
        files={"arquivo": ("contrato.txt", b"nao e pdf", "text/plain")},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "pdf" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_arquivo_contrato_rejeita_acima_do_cap(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review final: cap de tamanho do PDF -- monkeypatch do limite p/
    um valor pequeno para nao precisar gerar um arquivo de 10MB real."""
    import app.modules.financeiro_contratos.router as contratos_router

    monkeypatch.setattr(contratos_router, "MAX_CONTRATO_UPLOAD_BYTES", 10)

    resp = await api_client.post(
        "/api/v1/financeiro/contratos",
        json={
            "titulo": "Guard cap", "contraparte_nome": "Z",
            "tipo": "cliente", "data_inicio": "2026-01-01",
        },
        headers=auth_headers,
    )
    contrato_id = resp.json()["id"]

    resp = await api_client.post(
        f"/api/v1/financeiro/contratos/{contrato_id}/arquivo",
        files={
            "arquivo": (
                "contrato.pdf", b"%PDF-1.4 " + b"x" * 20, "application/pdf"
            )
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "limite" in resp.json()["detail"].lower()
