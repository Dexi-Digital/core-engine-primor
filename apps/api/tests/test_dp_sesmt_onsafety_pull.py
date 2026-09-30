"""Testes do pull SST OnSafety -> dossie (Squad 2, ADR-001)."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.onsafety.client import OnsafetyClient, OnsafetyError
from app.modules.dp_sesmt.models import (
    DOC_EMP_FICHA_EPI,
    DOC_EMP_NR06,
    DOC_EMP_NR35,
    DOC_EMP_TREINAMENTO_SST,
    DossieConsultaLog,
    Employee,
    EmployeeDocument,
)
from app.modules.dp_sesmt.onsafety_sync import pull_onsafety
from app.modules.obras.models import Obra

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
                    EmployeeDocument.employee_id == emp.id,
                    EmployeeDocument.tipo == DOC_EMP_FICHA_EPI,
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
        (
            await db_session.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.tipo == DOC_EMP_FICHA_EPI
                )
            )
        )
        .scalars()
        .all()
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
            "/api/v1/dp-sesmt/onsafety/pull?inline=true", headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is None
    assert body["exames_total"] == 8
    assert body["epis_total"] == 15
    assert body["treinos_total"] == 4


@pytest.mark.asyncio
async def test_endpoint_pull_enfileira_por_default(
    api_client, auth_headers, monkeypatch
):
    """Default e enfileirar: o pull varre a base inteira da OnSafety e
    nao cabe num request HTTP com a base de producao."""
    from app.main import app
    from app.modules.dp_sesmt.router import get_onsafety_dep
    from app.modules.manutencao_frota import service as frota_svc

    enviados: list[tuple[str, str]] = []

    class _Disp:
        def send_task(self, nome, queue=None, **kw):
            enviados.append((nome, queue))

    monkeypatch.setattr(frota_svc, "get_celery_dispatcher", lambda: _Disp())
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
    assert resp.json()["enfileirado"] is True
    assert enviados == [("worker.tasks.dp_sesmt.pull_onsafety", "dp_sesmt")]


@pytest.mark.asyncio
async def test_endpoint_pull_exige_auth(api_client):
    resp = await api_client.post("/api/v1/dp-sesmt/onsafety/pull")
    assert resp.status_code == 401


# --- Treinamentos (fonte: /v2/treinamentos_realizados) ---------------
#
# O pull percorre TREINAMENTOS e desce nos participantes. O caminho
# inverso nao serve: contra a base real a relacao `treinamentoRealizado`
# volta vazia, entao sigla/descricao/validade nunca chegariam.


async def _mock_treinos(client: OnsafetyClient) -> list[dict]:
    res = await client.list_treinamentos_realizados(page=0, size=100)
    return res["items"]


def _por_sigla(treinos: list[dict], sigla: str) -> dict:
    return next(t for t in treinos if t["sigla"] == sigla)


def _cpf_aprovado(treino: dict) -> str:
    p = next(p for p in treino["participantes"] if p["aprovado"])
    return p["trabalhador"]["cpf"]


async def _criar_obra(db: AsyncSession, codigo: str, nome: str) -> Obra:
    obra = Obra(codigo=codigo, nome=nome)
    db.add(obra)
    await db.commit()
    await db.refresh(obra)
    return obra


@pytest.mark.asyncio
async def test_pull_treinamento_cria_doc_de_nr_com_validade(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = _por_sigla(treinos, "NR 35")
    emp = await _criar_employee(db_session, _cpf_aprovado(alvo))

    await pull_onsafety(db_session, client, actor="t@t.com")

    doc = (
        await db_session.execute(
            select(EmployeeDocument).where(
                EmployeeDocument.employee_id == emp.id,
                EmployeeDocument.tipo == DOC_EMP_NR35,
            )
        )
    ).scalar_one()
    assert doc.source == "onsafety"
    assert doc.onsafety_external_id
    # A garantia central: NUNCA gravar NR sem validade -- validade None
    # e lida como "perene -> conforme" pelo diagnostico.
    assert doc.validade == date.fromisoformat(alvo["data_vencimento"])
    assert doc.emissao == date.fromisoformat(alvo["data_fim"])


@pytest.mark.asyncio
async def test_pull_treinamento_reprovado_nao_vira_documento(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    nr18 = _por_sigla(treinos, "NR 18")
    reprovado = next(p for p in nr18["participantes"] if not p["aprovado"])
    emp = await _criar_employee(
        db_session, reprovado["trabalhador"]["cpf"]
    )

    summary = await pull_onsafety(db_session, client, actor="t@t.com")

    docs = (
        (
            await db_session.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.onsafety_external_id
                    == str(reprovado["id"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert docs == []
    assert summary.treinos_reprovados >= 1
    assert emp.id is not None


@pytest.mark.asyncio
async def test_pull_treinamento_sem_validade_preserva_sem_avaliar_conformidade(
    db_session: AsyncSession,
):
    """NR mapeada, mas sem vencimento nem validade_dias: gravar viraria
    falso 'conforme' no diagnostico."""
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = _por_sigla(treinos, "NR 12")
    assert alvo["data_vencimento"] is None and alvo["validade_dias"] is None
    emp = await _criar_employee(db_session, _cpf_aprovado(alvo))

    summary = await pull_onsafety(db_session, client, actor="t@t.com")

    doc = (
        await db_session.execute(
            select(EmployeeDocument).where(
                EmployeeDocument.employee_id == emp.id,
                EmployeeDocument.tipo == DOC_EMP_TREINAMENTO_SST,
            )
        )
    ).scalar_one()
    assert doc.validade is None
    from app.modules.diagnostico.checklists import CHECKLIST_SST_FUNCIONARIO

    assert DOC_EMP_TREINAMENTO_SST not in {
        req.doc_tipo for req in CHECKLIST_SST_FUNCIONARIO
    }
    assert summary.treinos_sem_validade >= 1


@pytest.mark.asyncio
async def test_pull_treinamento_validade_por_dias_quando_falta_vencimento(
    db_session: AsyncSession, monkeypatch
):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = dict(
        _por_sigla(treinos, "NR 35"), data_vencimento=None, validade_dias=365
    )
    emp = await _criar_employee(db_session, _cpf_aprovado(alvo))

    async def _so_dias(*, page=0, size=100):
        return {"items": [alvo] if page == 0 else [], "total": 1,
                "page": page, "size": size, "source": "onsafety_mock"}

    monkeypatch.setattr(client, "list_treinamentos_realizados", _so_dias)
    await pull_onsafety(db_session, client, actor="t@t.com")

    doc = (
        await db_session.execute(
            select(EmployeeDocument).where(
                EmployeeDocument.employee_id == emp.id,
                EmployeeDocument.tipo == DOC_EMP_NR35,
            )
        )
    ).scalar_one()
    assert doc.validade == date.fromisoformat(alvo["data_fim"]) + timedelta(
        days=365
    )


@pytest.mark.asyncio
async def test_pull_treinamento_nr06_fica_no_dossie_sem_exigencia_universal(
    db_session: AsyncSession,
):
    """NR-6 e consultavel no dossie, mas nao e obrigatoria para todos."""
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    nr6 = _por_sigla(treinos, "NR 6")
    emp = await _criar_employee(db_session, _cpf_aprovado(nr6))

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
    assert any(d.tipo in {DOC_EMP_NR06, DOC_EMP_TREINAMENTO_SST} for d in docs)
    assert all(d.tipo != "OUTRO" for d in docs)
    from app.modules.diagnostico.checklists import CHECKLIST_SST_FUNCIONARIO

    assert DOC_EMP_NR06 not in {req.doc_tipo for req in CHECKLIST_SST_FUNCIONARIO}
    assert summary.treinos_nr_desconhecida == 0


@pytest.mark.asyncio
async def test_pull_treinamento_e_idempotente(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = _por_sigla(treinos, "NR 35")
    await _criar_employee(db_session, _cpf_aprovado(alvo))

    s1 = await pull_onsafety(db_session, client, actor="t@t.com")
    s2 = await pull_onsafety(db_session, client, actor="t@t.com")

    total = len(
        (
            await db_session.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.tipo == DOC_EMP_NR35
                )
            )
        )
        .scalars()
        .all()
    )
    assert total == 1
    assert s1.treinos_created >= 1
    assert s2.treinos_created == 0 and s2.treinos_updated >= 1


@pytest.mark.asyncio
async def test_pull_treinamento_vincula_obra_por_codigo_externo(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = _por_sigla(treinos, "NR 35")
    obra = await _criar_obra(
        db_session, alvo["projeto"]["codigo_externo"], "OBRA DE TESTE"
    )
    emp = await _criar_employee(db_session, _cpf_aprovado(alvo))

    await pull_onsafety(db_session, client, actor="t@t.com")

    doc = (
        await db_session.execute(
            select(EmployeeDocument).where(
                EmployeeDocument.employee_id == emp.id,
                EmployeeDocument.tipo == DOC_EMP_NR35,
            )
        )
    ).scalar_one()
    assert doc.obra_id == obra.id


@pytest.mark.asyncio
async def test_pull_treinamento_obra_ausente_nao_bloqueia(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = _por_sigla(treinos, "NR 35")
    emp = await _criar_employee(db_session, _cpf_aprovado(alvo))

    summary = await pull_onsafety(db_session, client, actor="t@t.com")

    doc = (
        await db_session.execute(
            select(EmployeeDocument).where(
                EmployeeDocument.employee_id == emp.id,
                EmployeeDocument.tipo == DOC_EMP_NR35,
            )
        )
    ).scalar_one()
    assert doc.obra_id is None
    assert summary.projeto_no_match >= 1


@pytest.mark.asyncio
async def test_pull_treinamento_registra_consulta_lgpd(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    treinos = await _mock_treinos(client)
    alvo = _por_sigla(treinos, "NR 35")
    emp = await _criar_employee(db_session, _cpf_aprovado(alvo))

    await pull_onsafety(db_session, client, actor="t@t.com")

    fontes = (
        (
            await db_session.execute(
                select(DossieConsultaLog.fonte).where(
                    DossieConsultaLog.employee_id == emp.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert "onsafety_treinamento" in fontes


# --- audit por registro (LGPD) ---------------------------------------------


async def _audits_registro(db: AsyncSession) -> list[AuditLog]:
    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.resource.in_(
                        ("dp_sesmt.employee", "dp_sesmt.employee_document")
                    )
                )
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_pull_aso_audita_before_after_so_campos_alterados(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(
        db_session,
        cpfs["exames"][0],
        aso_data=date(2000, 1, 1),
        aso_validade=date(2001, 1, 1),
        aso_resultado="apto",
    )

    summary = await pull_onsafety(db_session, client, actor="admin@primor.com")
    await db_session.refresh(emp)

    audits = [
        a
        for a in await _audits_registro(db_session)
        if a.resource == "dp_sesmt.employee"
    ]
    assert len(audits) == 1
    a = audits[0]
    assert a.action == "update"
    assert a.actor == "admin@primor.com"
    assert a.resource_id == str(emp.id)
    meta = json.loads(a.metadata_json)
    assert meta["source"] == "onsafety"
    changed = meta["changed"]
    assert changed["aso_data"] == {
        "from": "2000-01-01",
        "to": emp.aso_data.isoformat(),
    }
    assert changed["aso_validade"]["from"] == "2001-01-01"
    # resultado do mock e "apto" (1) -> nao mudou, nao entra no diff
    assert emp.aso_resultado == "apto"
    assert "aso_resultado" not in changed
    # CPF nunca vai no audit por registro
    assert emp.cpf not in a.metadata_json
    assert summary.audit_registros >= 1


@pytest.mark.asyncio
async def test_pull_documentos_auditados_na_criacao(db_session: AsyncSession):
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
    doc_audits = [
        a
        for a in await _audits_registro(db_session)
        if a.resource == "dp_sesmt.employee_document"
    ]
    assert docs
    # um create por documento criado, com o id real do documento
    assert {a.resource_id for a in doc_audits} == {str(d.id) for d in docs}
    assert all(a.action == "create" for a in doc_audits)
    meta = json.loads(doc_audits[0].metadata_json)
    assert meta["employee_id"] == emp.id
    assert meta["onsafety_external_id"]
    assert meta["changed"]["tipo"]["from"] is None
    assert summary.audit_registros >= len(doc_audits)


@pytest.mark.asyncio
async def test_pull_re_run_mesmo_dado_nao_gera_audit_por_registro(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    treinos = await _mock_treinos(client)
    await _criar_employee(db_session, cpfs["exames"][0])
    if cpfs["epis"][0] != cpfs["exames"][0]:
        await _criar_employee(db_session, cpfs["epis"][0])
    cpf_nr = _cpf_aprovado(_por_sigla(treinos, "NR 35"))
    if cpf_nr not in {cpfs["exames"][0], cpfs["epis"][0]}:
        await _criar_employee(db_session, cpf_nr)

    await pull_onsafety(db_session, client, actor="t@t.com")
    antes = len(await _audits_registro(db_session))
    assert antes > 0

    s2 = await pull_onsafety(db_session, client, actor="t@t.com")

    assert len(await _audits_registro(db_session)) == antes
    assert s2.audit_registros == 0
    # o resumo do run continua sendo gravado
    runs = (
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
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_pull_documento_alterado_audita_so_o_diff(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    cpfs = await _mock_cpfs(client)
    emp = await _criar_employee(db_session, cpfs["epis"][0])
    await pull_onsafety(db_session, client, actor="t@t.com")

    doc = (
        (
            await db_session.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.employee_id == emp.id,
                    EmployeeDocument.tipo == DOC_EMP_FICHA_EPI,
                )
            )
        )
        .scalars()
        .first()
    )
    numero_original = doc.numero
    doc.numero = "CA adulterado"
    await db_session.commit()
    antes = len(await _audits_registro(db_session))

    await pull_onsafety(db_session, client, actor="t@t.com")

    novos = (await _audits_registro(db_session))[antes:]
    assert len(novos) == 1
    assert novos[0].action == "update"
    assert novos[0].resource_id == str(doc.id)
    assert json.loads(novos[0].metadata_json)["changed"] == {
        "numero": {"from": "CA adulterado", "to": numero_original}
    }
