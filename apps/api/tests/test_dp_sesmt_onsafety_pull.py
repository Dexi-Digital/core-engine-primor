"""Testes do pull SST OnSafety -> dossie (Squad 2, ADR-001)."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.onsafety.client import OnsafetyClient, OnsafetyError
from app.modules.dp_sesmt.models import (
    DOC_EMP_FICHA_EPI,
    DossieConsultaLog,
    Employee,
    EmployeeDocument,
)
from app.modules.dp_sesmt.onsafety_sync import pull_onsafety

# --- helpers ---------------------------------------------------------------


async def _mock_cpfs(client: OnsafetyClient) -> dict[str, list[str]]:
    """CPFs dos datasets mock (deterministicos)."""
    exames = await client.list_exames_ocupacionais(page=0, size=100)
    epis = await client.list_controles_epi(page=0, size=100)
    return {
        "exames": [i["trabalhador"]["cpf"] for i in exames["items"]],
        "epis": [i["trabalhador"]["cpf"] for i in epis["items"]],
    }


async def _criar_employee(
    db: AsyncSession, cpf: str, **extra: object
) -> Employee:
    emp = Employee(
        cpf=cpf,
        nome_completo=f"FUNC {cpf[:4]}",
        cargo="Operador",
        **extra,
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


# --- ASO --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_aso_atualiza_dossie(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(db_session, cpfs["exames"][0])
    assert emp.aso_data is None

    summary = await pull_onsafety(db_session, client, actor="t@t.com")

    await db_session.refresh(emp)
    assert emp.aso_data is not None
    assert emp.aso_validade is not None
    assert emp.aso_resultado in {"apto", "inapto", "apto_restricoes"}
    assert summary.aso_updated >= 1
    assert summary.error is None
    # exames de trabalhadores sem cadastro local nao criam funcionario
    assert summary.aso_no_match > 0
    total_emps = (
        (await db_session.execute(select(Employee))).scalars().all()
    )
    assert len(total_emps) == 1


@pytest.mark.asyncio
async def test_pull_aso_nao_regride_dado_mais_novo(db_session: AsyncSession):
    # Dossie tem ASO manual de 2030 (mais novo que qualquer mock, que e
    # de 2025) -- o pull NAO pode sobrescrever.
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(
        db_session,
        cpfs["exames"][0],
        aso_data=date(2030, 1, 1),
        aso_validade=date(2031, 1, 1),
        aso_resultado="apto",
    )

    summary = await pull_onsafety(db_session, client, actor="t@t.com")

    await db_session.refresh(emp)
    assert emp.aso_data == date(2030, 1, 1)
    assert emp.aso_validade == date(2031, 1, 1)
    assert summary.aso_skipped_older >= 1


@pytest.mark.asyncio
async def test_pull_gera_log_lgpd_por_funcionario(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(db_session, cpfs["exames"][0])

    await pull_onsafety(db_session, client, actor="t@t.com")

    logs = (
        (
            await db_session.execute(
                select(DossieConsultaLog).where(
                    DossieConsultaLog.employee_id == emp.id
                )
            )
        )
        .scalars()
        .all()
    )
    fontes = {log.fonte for log in logs}
    assert "onsafety_aso" in fontes
    # 1 row por (employee, fonte) por run -- nao por item
    assert len([x for x in logs if x.fonte == "onsafety_aso"]) == 1


# --- EPI --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_epi_cria_documento_ficha_epi(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(db_session, cpfs["epis"][0])

    summary = await pull_onsafety(db_session, client, actor="t@t.com")

    docs = (
        (
            await db_session.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.employee_id == emp.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert docs, "pull deveria criar documento FICHA_EPI"
    doc = docs[0]
    assert doc.tipo == DOC_EMP_FICHA_EPI
    assert doc.source == "onsafety"
    assert doc.onsafety_external_id
    assert doc.numero and doc.numero.startswith("CA ")
    assert summary.epis_created == len(docs)


@pytest.mark.asyncio
async def test_pull_epi_idempotente_re_run_nao_duplica(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    await _criar_employee(db_session, cpfs["epis"][0])

    s1 = await pull_onsafety(db_session, client, actor="t@t.com")
    s2 = await pull_onsafety(db_session, client, actor="t@t.com")

    docs = (
        (await db_session.execute(select(EmployeeDocument))).scalars().all()
    )
    assert len(docs) == s1.epis_created  # re-run atualizou, nao criou
    assert s2.epis_created == 0
    assert s2.epis_updated == s1.epis_created


@pytest.mark.asyncio
async def test_pull_epi_nao_toca_documento_manual(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(db_session, cpfs["epis"][0])
    manual = EmployeeDocument(
        employee_id=emp.id,
        tipo=DOC_EMP_FICHA_EPI,
        numero="MANUAL-1",
        source="manual",
    )
    db_session.add(manual)
    await db_session.commit()

    await pull_onsafety(db_session, client, actor="t@t.com")

    await db_session.refresh(manual)
    assert manual.numero == "MANUAL-1"
    assert manual.source == "manual"
    assert manual.onsafety_external_id is None


# --- resiliencia / auditoria -------------------------------------------------


class _BoomClient(OnsafetyClient):
    def __init__(self) -> None:
        super().__init__(api_token="t-x")

    async def list_exames_ocupacionais(self, **kwargs):
        raise OnsafetyError("upstream fora")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_pull_erro_upstream_nao_propaga(db_session: AsyncSession):
    summary = await pull_onsafety(
        db_session, _BoomClient(), actor="t@t.com"
    )
    assert summary.error and "upstream fora" in summary.error

    logs = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "dp_sesmt.onsafety_pull"
                )
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[-1].action == "error"


@pytest.mark.asyncio
async def test_pull_audit_do_run_com_summary(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    await pull_onsafety(db_session, client, actor="admin@primor.com")
    logs = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "dp_sesmt.onsafety_pull"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].action == "pull"
    assert logs[0].actor == "admin@primor.com"
    assert '"exames_total"' in logs[0].metadata_json


# --- endpoint ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_pull_dispatch(api_client, auth_headers):
    from app.main import app
    from app.modules.dp_sesmt.router import get_onsafety_dep

    app.dependency_overrides[get_onsafety_dep] = lambda: OnsafetyClient(
        api_token=None
    )
    try:
        resp = await api_client.post(
            "/api/v1/dp-sesmt/onsafety/pull", headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is None
    assert body["exames_total"] == 8
    assert body["epis_total"] == 15


@pytest.mark.asyncio
async def test_endpoint_pull_exige_auth(api_client):
    resp = await api_client.post("/api/v1/dp-sesmt/onsafety/pull")
    assert resp.status_code == 401
