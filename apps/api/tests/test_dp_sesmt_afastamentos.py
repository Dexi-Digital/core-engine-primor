"""Testes do D4 - Afastamentos INSS.

Cobre:

- CRUD com `audit_log.actor=current_user.email` (LGPD).
- 401 sem JWT em rotas de mutacao (POST/PUT/DELETE/dispatch).
- Validators (beneficio_tipo / status canonico).
- Computa `dcb_status` / `pericia_status`.
- Janelas de alerta DCB (30/15/7/0) e pericia (15/7/0).
- Idempotencia: 2 dispatches no mesmo dia mandam 1 email por
  (afastamento, kind, janela).
- Apenas afastamentos `em_andamento` recebem alerta.
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.resend.client import ResendClient
from app.modules.dp_sesmt.afastamentos import (
    JANELAS_DCB,
    JANELAS_PERICIA,
    compute_dcb_status,
    compute_pericia_status,
    dispatch_afastamento_alerts,
    janela_for,
)
from app.modules.dp_sesmt.models import (
    AFASTAMENTO_EM_ANDAMENTO,
    AFASTAMENTO_ENCERRADO,
    BENEFICIO_B31,
    Afastamento,
    AfastamentoAlertaLog,
    Employee,
)

# --- domain helpers --------------------------------------------------------


def test_compute_dcb_status_buckets() -> None:
    today = date(2026, 4, 25)
    assert compute_dcb_status(None, today=today) is None
    assert compute_dcb_status(today + timedelta(days=60), today=today) == "vigente"
    assert compute_dcb_status(today + timedelta(days=30), today=today) == "vencendo"
    assert compute_dcb_status(today + timedelta(days=1), today=today) == "vencendo"
    assert compute_dcb_status(today, today=today) == "vencendo"
    assert compute_dcb_status(today - timedelta(days=1), today=today) == "vencido"


def test_compute_pericia_status_buckets() -> None:
    today = date(2026, 4, 25)
    assert compute_pericia_status(None, today=today) is None
    assert compute_pericia_status(today + timedelta(days=20), today=today) == "vigente"
    assert compute_pericia_status(today + timedelta(days=15), today=today) == "vencendo"
    assert compute_pericia_status(today, today=today) == "vencendo"
    assert compute_pericia_status(today - timedelta(days=1), today=today) == "vencido"


def test_janela_for_picks_smallest_active() -> None:
    today = date(2026, 4, 25)
    # DCB com 12 dias -- cai na janela 15 (menor >= 12).
    assert janela_for(today + timedelta(days=12), JANELAS_DCB, today=today) == 15
    # DCB com 31 dias -- nenhuma janela aplicavel ainda.
    assert janela_for(today + timedelta(days=31), JANELAS_DCB, today=today) is None
    # DCB hoje -- janela 0.
    assert janela_for(today, JANELAS_DCB, today=today) == 0
    # Pericia: 8 dias -> janela 15.
    assert janela_for(today + timedelta(days=8), JANELAS_PERICIA, today=today) == 15
    # Pericia: 4 dias -> janela 7.
    assert janela_for(today + timedelta(days=4), JANELAS_PERICIA, today=today) == 7


def test_janelas_include_zero() -> None:
    assert 0 in JANELAS_DCB
    assert 0 in JANELAS_PERICIA


# --- fixtures auxiliares ---------------------------------------------------


def _mock_resend(
    captured: list[httpx.Request], message_id: str = "msg_inss_xyz"
) -> ResendClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": message_id})

    return ResendClient(
        api_key="re_test",
        client=AsyncClient(
            base_url="https://mock.resend",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer re_test"},
        ),
    )


async def _create_employee(
    db: AsyncSession,
    *,
    cpf: str = "11144477735",
    nome: str = "Joao da Silva",
) -> Employee:
    emp = Employee(
        cpf=cpf,
        nome_completo=nome,
        cargo="Pedreiro",
        obra="Obra A",
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


async def _create_afastamento(
    db: AsyncSession,
    employee_id: int,
    *,
    beneficio_tipo: str = BENEFICIO_B31,
    data_inicio: date | None = None,
    dcb: date | None = None,
    data_pericia: date | None = None,
    status: str = AFASTAMENTO_EM_ANDAMENTO,
) -> Afastamento:
    row = Afastamento(
        employee_id=employee_id,
        beneficio_tipo=beneficio_tipo,
        data_inicio=data_inicio or date(2026, 1, 1),
        dcb=dcb,
        data_pericia=data_pericia,
        status=status,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# --- HTTP / auth -----------------------------------------------------------


@pytest.mark.asyncio
async def test_create_afastamento_requires_auth(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    emp = await _create_employee(db_session)
    resp = await api_client.post(
        "/api/v1/dp-sesmt/afastamentos",
        json={
            "employee_id": emp.id,
            "beneficio_tipo": "B31",
            "data_inicio": "2026-04-01",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dispatch_afastamento_alerts_requires_auth(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/afastamentos/alerts/dispatch",
        json=["foo@bar.com"],
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_afastamento_records_audit_actor(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    emp = await _create_employee(db_session)
    resp = await api_client.post(
        "/api/v1/dp-sesmt/afastamentos",
        json={
            "employee_id": emp.id,
            "beneficio_tipo": "B91",
            "data_inicio": "2026-04-01",
            "dcb": "2026-05-15",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["beneficio_tipo"] == "B91"
    assert body["status"] == AFASTAMENTO_EM_ANDAMENTO
    assert body["dcb_status"] in {"vigente", "vencendo", "vencido"}

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource == "dp_sesmt.afastamento"
            )
        )
    ).scalars().all()
    assert len(audit) == 1
    assert audit[0].actor == "test-admin@primor.com"
    assert audit[0].action == "create"


@pytest.mark.asyncio
async def test_create_afastamento_rejects_invalid_employee(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/afastamentos",
        json={
            "employee_id": 999999,
            "beneficio_tipo": "B31",
            "data_inicio": "2026-04-01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_afastamento_rejects_invalid_beneficio_tipo(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    emp = await _create_employee(db_session)
    resp = await api_client.post(
        "/api/v1/dp-sesmt/afastamentos",
        json={
            "employee_id": emp.id,
            "beneficio_tipo": "XYZ",
            "data_inicio": "2026-04-01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_afastamento_records_audit(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    emp = await _create_employee(db_session)
    af = await _create_afastamento(db_session, emp.id, dcb=date(2026, 5, 1))
    resp = await api_client.put(
        f"/api/v1/dp-sesmt/afastamentos/{af.id}",
        json={"status": AFASTAMENTO_ENCERRADO, "data_retorno": "2026-05-02"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource == "dp_sesmt.afastamento"
            )
        )
    ).scalars().all()
    assert any(
        a.action == "update" and a.actor == "test-admin@primor.com"
        for a in audit
    )


@pytest.mark.asyncio
async def test_delete_afastamento_records_audit(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    emp = await _create_employee(db_session)
    af = await _create_afastamento(db_session, emp.id)
    resp = await api_client.delete(
        f"/api/v1/dp-sesmt/afastamentos/{af.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 204
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource == "dp_sesmt.afastamento"
            )
        )
    ).scalars().all()
    assert any(
        a.action == "delete" and a.actor == "test-admin@primor.com"
        for a in audit
    )


@pytest.mark.asyncio
async def test_list_filters_by_employee(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    a = await _create_employee(db_session, cpf="11144477735", nome="A")
    b = await _create_employee(db_session, cpf="11144477736", nome="B")
    await _create_afastamento(db_session, a.id)
    await _create_afastamento(db_session, b.id)
    resp = await api_client.get(
        f"/api/v1/dp-sesmt/afastamentos?employee_id={a.id}"
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["employee_id"] == a.id


# --- alerts ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_sends_dcb_in_window(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    emp = await _create_employee(db_session)
    await _create_afastamento(
        db_session, emp.id, dcb=today + timedelta(days=10)
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_afastamento_alerts(
        db_session, resend, recipients=["rh@primor.com"], today=today
    )
    await resend.aclose()
    assert summary.sent == 1
    assert summary.failed == 0
    assert len(captured) == 1
    body = captured[0].read().decode()
    # Subject usa dias REAIS (10), nao a janela (15).
    assert "10 dia" in body.lower()


@pytest.mark.asyncio
async def test_dispatch_idempotent_same_window(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    emp = await _create_employee(db_session)
    await _create_afastamento(
        db_session, emp.id, dcb=today + timedelta(days=5)
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    s1 = await dispatch_afastamento_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    s2 = await dispatch_afastamento_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()
    assert s1.sent == 1
    assert s2.sent == 0
    assert s2.skipped == 1
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_status_nao_em_andamento(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    emp = await _create_employee(db_session)
    await _create_afastamento(
        db_session,
        emp.id,
        dcb=today + timedelta(days=5),
        status=AFASTAMENTO_ENCERRADO,
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_afastamento_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()
    assert summary.sent == 0
    assert summary.total_afastamentos == 0  # SQL filtrou


@pytest.mark.asyncio
async def test_dispatch_pericia_window(db_session: AsyncSession) -> None:
    """Pericia entra na janela 15/7/0. DCB sem data nao gera alerta de DCB."""
    today = date(2026, 4, 25)
    emp = await _create_employee(db_session)
    await _create_afastamento(
        db_session,
        emp.id,
        dcb=None,
        data_pericia=today + timedelta(days=4),  # janela 7
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_afastamento_alerts(
        db_session, resend, recipients=["rh@primor.com"], today=today
    )
    await resend.aclose()
    assert summary.sent == 1
    assert len(captured) == 1
    body = captured[0].read().decode()
    assert "pericia" in body.lower()
    log = (
        await db_session.execute(select(AfastamentoAlertaLog))
    ).scalars().first()
    assert log is not None
    assert log.kind == "pericia"


@pytest.mark.asyncio
async def test_dispatch_dcb_e_pericia_disparam_independentes(
    db_session: AsyncSession,
) -> None:
    """DCB em janela 7 + pericia em janela 15 -> 2 emails."""
    today = date(2026, 4, 25)
    emp = await _create_employee(db_session)
    await _create_afastamento(
        db_session,
        emp.id,
        dcb=today + timedelta(days=5),
        data_pericia=today + timedelta(days=12),
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_afastamento_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()
    assert summary.sent == 2
    logs = (
        await db_session.execute(select(AfastamentoAlertaLog))
    ).scalars().all()
    assert sorted(log.kind for log in logs) == ["dcb", "pericia"]


@pytest.mark.asyncio
async def test_dispatch_empty_recipients_returns(
    db_session: AsyncSession,
) -> None:
    emp = await _create_employee(db_session)
    await _create_afastamento(
        db_session, emp.id, dcb=date(2026, 4, 30)
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_afastamento_alerts(
        db_session, resend, recipients=[], today=date(2026, 4, 25)
    )
    await resend.aclose()
    assert summary.total_afastamentos == 0
    assert summary.sent == 0
    assert len(captured) == 0
