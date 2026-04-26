"""Tests para o motor de Diagnostico Documental (D1)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.modules.diagnostico.checklists import (
    CHECKLIST_DP_FUNCIONARIO,
    applicable_dp_requirements,
)
from app.modules.diagnostico.models import (
    AREA_DP,
    AREA_EMPRESA,
    AREA_FROTA,
    AREA_OBRA,
    AREA_SST,
    FINDING_AUSENTE,
    FINDING_OK,
    FINDING_VENCENDO,
    FINDING_VENCIDO,
    DiagnosticoFinding,
)
from app.modules.diagnostico.runner import run_diagnostico
from app.modules.dp_sesmt.models import (
    DOC_EMP_NR10,
    DOC_EMP_NR12,
    DOC_EMP_NR18,
    DOC_EMP_NR35,
    DOC_EMP_OS,
    DOC_EMP_TIPOS_VALIDOS,
    DOC_EMP_TOXICOLOGICO,
    Employee,
    EmployeeDocument,
)
from app.modules.licitacoes.models import (
    DOC_EMPRESA_CONTRATO_SOCIAL,
    DOC_EMPRESA_SICAF,
    CertidaoEmpresa,
    EmpresaDocumento,
)
from app.modules.manutencao_frota.models import (
    DOC_CRLV,
    DOC_IPVA,
    DOC_LICENCIAMENTO,
    DOC_SEGURO,
    DocumentoVeiculo,
    Veiculo,
)
from app.modules.obras.models import (
    DOC_ALVARA,
    DOC_ART,
    DOC_DIARIO_OBRA,
    Obra,
    ObraDocumento,
)

# ----------------------------- helpers ------------------------------------


async def _create_employee(
    db: AsyncSession,
    *,
    nome: str = "Joao Operario",
    cpf: str = "111.222.333-44",
    is_motorista: bool = False,
    is_operador_maquina: bool = False,
    is_admin_office: bool = False,
    is_alturas: bool = False,
    is_eletricista: bool = False,
    aso_validade: date | None = None,
) -> Employee:
    emp = Employee(
        cpf=cpf,
        nome_completo=nome,
        cargo="Pedreiro",
        is_motorista=is_motorista,
        is_operador_maquina=is_operador_maquina,
        is_admin_office=is_admin_office,
        is_alturas=is_alturas,
        is_eletricista=is_eletricista,
        aso_data=date.today() - timedelta(days=30) if aso_validade else None,
        aso_validade=aso_validade,
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


async def _create_emp_doc(
    db: AsyncSession,
    employee_id: int,
    tipo: str,
    *,
    validade: date | None = None,
) -> None:
    db.add(
        EmployeeDocument(
            employee_id=employee_id,
            tipo=tipo,
            validade=validade,
        )
    )
    await db.commit()


async def _create_veiculo(
    db: AsyncSession, placa: str = "ABC1D23"
) -> Veiculo:
    v = Veiculo(placa=placa, marca="Ford", modelo="Cargo", status="ativo")
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _create_obra(
    db: AsyncSession, codigo: str = "OBRA-001"
) -> Obra:
    o = Obra(codigo=codigo, nome="Construcao Sede", status="ativa")
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


# ----------------------------- domain helpers tests -----------------------


def test_checklist_dp_doc_tipos_aceitos_pela_api() -> None:
    """Regressao: tipos do checklist DP precisam ser aceitos pela
    API de EmployeeDocument.

    Sem isso, requirements como CTPS / CONTRATO_TRABALHO /
    FICHA_REGISTRO seriam impossiveis de satisfazer (POST com tipo
    nao listado em DOC_EMP_TIPOS_VALIDOS volta 422), entao todo
    funcionario apareceria com `ausente` permanente para esses
    requirements -- inflando o ausente_count e deflacionando a %
    de conformidade.

    ASO continua flat em `Employee.aso_*` (compat com A.2 alertas em
    prod) e por isso e o unico tipo do checklist DP que nao precisa
    estar em DOC_EMP_TIPOS_VALIDOS.
    """
    tipos_no_checklist = {req.doc_tipo for req in CHECKLIST_DP_FUNCIONARIO}
    # ASO e populado em colunas dedicadas em Employee, nao em
    # EmployeeDocument -- ver runner._evaluate_dp_employee.
    tipos_via_employee_document = tipos_no_checklist - {"ASO"}
    faltantes = tipos_via_employee_document - DOC_EMP_TIPOS_VALIDOS
    assert not faltantes, (
        f"Tipos do checklist DP que a API rejeita: {sorted(faltantes)}. "
        "Adicione em DOC_EMP_TIPOS_VALIDOS + DOC_EMP_LABELS."
    )


@pytest.mark.asyncio
async def test_applicable_dp_requirements_motorista_only(
    db_session: AsyncSession,
) -> None:
    """is_motorista deve adicionar TOXICOLOGICO; sem esse flag, nao."""
    emp = await _create_employee(
        db_session, cpf="000.000.000-01", is_motorista=False
    )
    reqs = applicable_dp_requirements(emp)
    tipos = {r.doc_tipo for r in reqs}
    assert DOC_EMP_TOXICOLOGICO not in tipos

    emp2 = await _create_employee(
        db_session, cpf="000.000.000-02", is_motorista=True
    )
    reqs2 = applicable_dp_requirements(emp2)
    tipos2 = {r.doc_tipo for r in reqs2}
    assert DOC_EMP_TOXICOLOGICO in tipos2


@pytest.mark.asyncio
async def test_applicable_dp_requirements_operador_exige_nr12(
    db_session: AsyncSession,
) -> None:
    emp = await _create_employee(
        db_session, cpf="000.000.000-03", is_operador_maquina=True
    )
    reqs = applicable_dp_requirements(emp)
    tipos = {r.doc_tipo for r in reqs}
    assert DOC_EMP_NR12 in tipos


@pytest.mark.asyncio
async def test_applicable_dp_requirements_alturas_exige_nr35(
    db_session: AsyncSession,
) -> None:
    emp = await _create_employee(
        db_session, cpf="000.000.000-04", is_alturas=True
    )
    reqs = applicable_dp_requirements(emp)
    assert DOC_EMP_NR35 in {r.doc_tipo for r in reqs}


@pytest.mark.asyncio
async def test_applicable_dp_requirements_eletricista_exige_nr10(
    db_session: AsyncSession,
) -> None:
    emp = await _create_employee(
        db_session, cpf="000.000.000-05", is_eletricista=True
    )
    reqs = applicable_dp_requirements(emp)
    assert DOC_EMP_NR10 in {r.doc_tipo for r in reqs}


@pytest.mark.asyncio
async def test_applicable_dp_requirements_admin_office_sem_nr18(
    db_session: AsyncSession,
) -> None:
    """Funcionarios de escritorio nao precisam de NR-18."""
    emp = await _create_employee(
        db_session, cpf="000.000.000-06", is_admin_office=True
    )
    reqs = applicable_dp_requirements(emp)
    assert DOC_EMP_NR18 not in {r.doc_tipo for r in reqs}


# ----------------------------- runner tests -------------------------------


@pytest.mark.asyncio
async def test_run_diagnostico_employee_aso_ausente(
    db_session: AsyncSession,
) -> None:
    """Funcionario sem ASO deve gerar finding ausente."""
    await _create_employee(db_session, aso_validade=None)
    run = await run_diagnostico(db_session, scope="dp", actor="test@x.com")
    assert run.status == "done"
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == "ASO",
        )
    )
    findings = list(res.scalars().all())
    assert len(findings) == 1
    assert findings[0].status == FINDING_AUSENTE


@pytest.mark.asyncio
async def test_run_diagnostico_employee_aso_vencido(
    db_session: AsyncSession,
) -> None:
    await _create_employee(
        db_session, aso_validade=date.today() - timedelta(days=10)
    )
    run = await run_diagnostico(db_session, scope="dp")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == "ASO",
        )
    )
    findings = list(res.scalars().all())
    assert findings[0].status == FINDING_VENCIDO


@pytest.mark.asyncio
async def test_run_diagnostico_employee_aso_vencendo(
    db_session: AsyncSession,
) -> None:
    await _create_employee(
        db_session, aso_validade=date.today() + timedelta(days=15)
    )
    run = await run_diagnostico(db_session, scope="dp")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == "ASO",
        )
    )
    findings = list(res.scalars().all())
    assert findings[0].status == FINDING_VENCENDO
    assert findings[0].dias_para_vencimento == 15


@pytest.mark.asyncio
async def test_run_diagnostico_employee_aso_ok(
    db_session: AsyncSession,
) -> None:
    await _create_employee(
        db_session, aso_validade=date.today() + timedelta(days=180)
    )
    run = await run_diagnostico(db_session, scope="dp")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == "ASO",
        )
    )
    assert list(res.scalars().all())[0].status == FINDING_OK


@pytest.mark.asyncio
async def test_run_diagnostico_motorista_exige_toxicologico(
    db_session: AsyncSession,
) -> None:
    await _create_employee(db_session, is_motorista=True)
    run = await run_diagnostico(db_session, scope="sst")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == DOC_EMP_TOXICOLOGICO,
        )
    )
    findings = list(res.scalars().all())
    assert len(findings) == 1
    assert findings[0].status == FINDING_AUSENTE
    assert findings[0].area == AREA_SST


@pytest.mark.asyncio
async def test_run_diagnostico_nao_motorista_sem_toxicologico(
    db_session: AsyncSession,
) -> None:
    await _create_employee(db_session, is_motorista=False)
    run = await run_diagnostico(db_session, scope="sst")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == DOC_EMP_TOXICOLOGICO,
        )
    )
    assert len(list(res.scalars().all())) == 0


@pytest.mark.asyncio
async def test_run_diagnostico_doc_recente_overrides_antigo(
    db_session: AsyncSession,
) -> None:
    """Documento mais recente sobrescreve antigo no calculo de status."""
    emp = await _create_employee(db_session)
    # antigo (vencido)
    await _create_emp_doc(
        db_session,
        emp.id,
        DOC_EMP_OS,
        validade=date.today() - timedelta(days=30),
    )
    # recente (ok)
    await _create_emp_doc(
        db_session,
        emp.id,
        DOC_EMP_OS,
        validade=date.today() + timedelta(days=200),
    )
    run = await run_diagnostico(db_session, scope="sst")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.doc_tipo == DOC_EMP_OS,
        )
    )
    findings = list(res.scalars().all())
    assert findings[0].status == FINDING_OK


@pytest.mark.asyncio
async def test_run_diagnostico_veiculo_documentos(
    db_session: AsyncSession,
) -> None:
    veiculo = await _create_veiculo(db_session)
    db_session.add(
        DocumentoVeiculo(
            veiculo_id=veiculo.id,
            tipo=DOC_CRLV,
            validade=date.today() + timedelta(days=180),
        )
    )
    await db_session.commit()
    run = await run_diagnostico(db_session, scope="frota")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.area == AREA_FROTA,
        )
    )
    rows = list(res.scalars().all())
    by_tipo = {r.doc_tipo: r for r in rows}
    assert by_tipo[DOC_CRLV].status == FINDING_OK
    # demais (IPVA, LICENCIAMENTO, SEGURO, DPVAT) ausentes
    assert by_tipo[DOC_IPVA].status == FINDING_AUSENTE
    assert by_tipo[DOC_LICENCIAMENTO].status == FINDING_AUSENTE
    assert by_tipo[DOC_SEGURO].status == FINDING_AUSENTE


@pytest.mark.asyncio
async def test_run_diagnostico_empresa_certidao_e_doc(
    db_session: AsyncSession,
) -> None:
    """Empresa com CND_FEDERAL ok e SICAF ausente."""
    cnpj = "12.345.678/0001-99"
    db_session.add(
        CertidaoEmpresa(
            empresa_cnpj=cnpj,
            tipo="CND_FEDERAL",
            validade=date.today() + timedelta(days=90),
        )
    )
    db_session.add(
        EmpresaDocumento(
            empresa_cnpj=cnpj,
            tipo=DOC_EMPRESA_CONTRATO_SOCIAL,
        )
    )
    await db_session.commit()
    run = await run_diagnostico(db_session, scope="empresa")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.area == AREA_EMPRESA,
        )
    )
    rows = list(res.scalars().all())
    by_tipo = {r.doc_tipo: r for r in rows}
    assert by_tipo["CND_FEDERAL"].status == FINDING_OK
    assert by_tipo[DOC_EMPRESA_CONTRATO_SOCIAL].status == FINDING_OK
    assert by_tipo[DOC_EMPRESA_SICAF].status == FINDING_AUSENTE


@pytest.mark.asyncio
async def test_run_diagnostico_obra_documentos(
    db_session: AsyncSession,
) -> None:
    obra = await _create_obra(db_session)
    db_session.add(
        ObraDocumento(
            obra_id=obra.id,
            tipo=DOC_ART,
            validade=date.today() + timedelta(days=365),
        )
    )
    db_session.add(
        ObraDocumento(
            obra_id=obra.id,
            tipo=DOC_ALVARA,
            validade=date.today() - timedelta(days=10),  # vencido
        )
    )
    await db_session.commit()
    run = await run_diagnostico(db_session, scope="obra")
    res = await db_session.execute(
        select(DiagnosticoFinding).where(
            DiagnosticoFinding.run_id == run.id,
            DiagnosticoFinding.area == AREA_OBRA,
        )
    )
    rows = list(res.scalars().all())
    by_tipo = {r.doc_tipo: r for r in rows}
    assert by_tipo[DOC_ART].status == FINDING_OK
    assert by_tipo[DOC_ALVARA].status == FINDING_VENCIDO
    assert by_tipo[DOC_DIARIO_OBRA].status == FINDING_AUSENTE


@pytest.mark.asyncio
async def test_run_diagnostico_summary_counts(
    db_session: AsyncSession,
) -> None:
    """Contadores do run batem com numero de findings."""
    await _create_employee(db_session, cpf="11.111.111-11")
    await _create_employee(db_session, cpf="22.222.222-22")
    run = await run_diagnostico(db_session, scope="dp")
    assert run.total_findings > 0
    assert (
        run.ok_count
        + run.ausente_count
        + run.vencido_count
        + run.vencendo_count
        == run.total_findings
    )
    assert run.summary_json is not None
    assert AREA_DP in run.summary_json


@pytest.mark.asyncio
async def test_run_diagnostico_grava_audit_log(
    db_session: AsyncSession,
) -> None:
    await _create_employee(db_session)
    run = await run_diagnostico(
        db_session, scope="dp", actor="admin@primor.com"
    )
    res = await db_session.execute(
        select(AuditLog).where(
            AuditLog.resource == "diagnostico.run",
            AuditLog.resource_id == str(run.id),
        )
    )
    audits = list(res.scalars().all())
    assert len(audits) == 1
    assert audits[0].actor == "admin@primor.com"
    assert audits[0].action == "run"


# ----------------------------- HTTP API tests -----------------------------


@pytest.mark.asyncio
async def test_post_run_requires_auth(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.post("/api/v1/diagnostico/run")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_run_authenticated(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    await _create_employee(db_session)
    resp = await api_client.post(
        "/api/v1/diagnostico/run",
        headers=auth_headers,
        json={"scope": "dp"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "done"
    assert data["scope"] == "dp"
    assert data["triggered_by"] == "test-admin@primor.com"
    assert data["total_findings"] >= 1


@pytest.mark.asyncio
async def test_post_run_invalid_scope_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/diagnostico/run",
        headers=auth_headers,
        json={"scope": "xpto"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_runs_list(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    await _create_employee(db_session)
    await api_client.post(
        "/api/v1/diagnostico/run",
        headers=auth_headers,
        json={"scope": "dp"},
    )
    resp = await api_client.get("/api/v1/diagnostico/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) >= 1


@pytest.mark.asyncio
async def test_get_run_detail_grouped_by_area(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    await _create_employee(db_session, is_motorista=True)
    create_resp = await api_client.post(
        "/api/v1/diagnostico/run",
        headers=auth_headers,
        json={"scope": "all"},
    )
    run_id = create_resp.json()["id"]
    resp = await api_client.get(f"/api/v1/diagnostico/runs/{run_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["run"]["id"] == run_id
    assert "findings_by_area" in detail
    # DP e SST devem aparecer (motorista)
    assert "dp" in detail["findings_by_area"] or "sst" in detail["findings_by_area"]


@pytest.mark.asyncio
async def test_export_run_csv_requires_auth(
    api_client: AsyncClient, db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    await _create_employee(db_session)
    create_resp = await api_client.post(
        "/api/v1/diagnostico/run",
        headers=auth_headers,
        json={"scope": "dp"},
    )
    run_id = create_resp.json()["id"]

    # Sem auth: 401
    resp_unauth = await api_client.get(
        f"/api/v1/diagnostico/runs/{run_id}/export.csv"
    )
    assert resp_unauth.status_code == 401

    # Com auth: 200 + content-type CSV + BOM
    resp = await api_client.get(
        f"/api/v1/diagnostico/runs/{run_id}/export.csv",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    body = resp.content.decode("utf-8")
    assert body.startswith("\ufeff"), "deve comecar com BOM utf-8"
    assert "area;entity_type" in body  # cabecalho


@pytest.mark.asyncio
async def test_get_run_detail_404(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/diagnostico/runs/99999")
    assert resp.status_code == 404


# --- Obras CRUD tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_obra_audit_actor(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    resp = await api_client.post(
        "/api/v1/obras",
        headers=auth_headers,
        json={"codigo": "OBRA-XYZ", "nome": "Construcao 123"},
    )
    assert resp.status_code == 201
    res = await db_session.execute(
        select(AuditLog).where(AuditLog.resource == "obras.obra")
    )
    audits = list(res.scalars().all())
    assert audits[0].actor == "test-admin@primor.com"
    assert audits[0].action == "create"


@pytest.mark.asyncio
async def test_create_obra_requires_auth(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/obras", json={"codigo": "X", "nome": "Y"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_obra_documento(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    obra = await _create_obra(db_session, codigo="OBRA-TEST")
    resp = await api_client.post(
        f"/api/v1/obras/{obra.id}/documentos",
        headers=auth_headers,
        json={
            "tipo": DOC_ART,
            "numero": "12345",
            "validade": (date.today() + timedelta(days=365)).isoformat(),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["tipo"] == DOC_ART


# --- Employee documentos -------------------------------------------------


@pytest.mark.asyncio
async def test_create_employee_document(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    emp = await _create_employee(db_session, cpf="999.888.777-66")
    resp = await api_client.post(
        f"/api/v1/dp-sesmt/employees/{emp.id}/documentos",
        headers=auth_headers,
        json={"tipo": DOC_EMP_NR18, "numero": "CERT-001"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tipo"] == DOC_EMP_NR18
    assert body["employee_id"] == emp.id


@pytest.mark.asyncio
async def test_employee_document_invalid_tipo_422(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    emp = await _create_employee(db_session, cpf="999.888.777-77")
    resp = await api_client.post(
        f"/api/v1/dp-sesmt/employees/{emp.id}/documentos",
        headers=auth_headers,
        json={"tipo": "FOO_INVALIDO"},
    )
    assert resp.status_code == 422


# --- Empresa documentos --------------------------------------------------


@pytest.mark.asyncio
async def test_create_empresa_documento(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    resp = await api_client.post(
        "/api/v1/empresa-documentos",
        headers=auth_headers,
        json={
            "empresa_cnpj": "11.111.111/0001-11",
            "tipo": DOC_EMPRESA_CONTRATO_SOCIAL,
        },
    )
    assert resp.status_code == 201
    res = await db_session.execute(
        select(AuditLog).where(
            AuditLog.resource == "licitacoes.empresa_documento"
        )
    )
    audits = list(res.scalars().all())
    assert audits[0].actor == "test-admin@primor.com"


# --- Afastamento midnight consistency (Devin Review #22 finding) --------


@pytest.mark.asyncio
async def test_to_afastamento_read_consistency_today(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """status e dias_para_dcb devem usar o mesmo `today` -- garante
    consistencia se o request cruzar meia-noite. Aqui apenas
    verificamos que o response tem ambos os campos.
    """
    emp = await _create_employee(db_session, cpf="000.111.222-99")
    target = date.today() + timedelta(days=15)
    resp = await api_client.post(
        "/api/v1/dp-sesmt/afastamentos",
        headers=auth_headers,
        json={
            "employee_id": emp.id,
            "beneficio_tipo": "B31",
            "data_inicio": date.today().isoformat(),
            "dcb": target.isoformat(),
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    # Status "vencendo" + dias=15 sao consistentes (mesmo today)
    assert body["dcb_status"] == "vencendo"
    assert body["dias_para_dcb"] == 15
