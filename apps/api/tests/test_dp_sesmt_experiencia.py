"""Alerta de fim de contrato de experiencia (demanda #2).

Cobre deteccao robusta do `tipo_contrato` (texto livre), premissa
45+45, contagem de dias (admissao = dia 1), janelas 15/7/0,
idempotencia via `chave_idempotencia` e o endpoint de leitura.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt.experiencia import (
    MARCO_EFETIVACAO,
    MARCO_PRORROGACAO,
    compute_prazo,
    dispatch_experiencia_alerts,
    is_contrato_experiencia,
    is_tipo_incompativel,
    list_prazos_experiencia,
    parse_periodos,
    recipients_from_env,
)
from app.modules.dp_sesmt.models import (
    DOC_EMP_CONTRATO_EXPERIENCIA,
    STATUS_DESLIGADO,
    Employee,
    EmployeeDocument,
)
from app.modules.notificacoes.models import CATEGORIA_DP, Notificacao

TODAY = date(2026, 9, 30)

# --- regras puras -----------------------------------------------------------


@pytest.mark.parametrize(
    "tipo",
    [
        "Experiência",
        "EXPERIENCIA",
        "CLT - Experiência",
        "clt experiencia 45+45",
        "Contrato de experiencia",
        "CLT (exp.)",
        "exp 30+60",
    ],
)
def test_detecta_experiencia(tipo: str) -> None:
    assert is_contrato_experiencia(tipo)


@pytest.mark.parametrize("tipo", [None, "", "CLT", "PJ", "Estágio", "expediente"])
def test_nao_detecta_experiencia(tipo: str | None) -> None:
    assert not is_contrato_experiencia(tipo)


def test_tipos_incompativeis() -> None:
    assert is_tipo_incompativel("PJ")
    assert is_tipo_incompativel("Estágio")
    assert is_tipo_incompativel("Jovem Aprendiz")
    assert not is_tipo_incompativel("CLT")
    assert not is_tipo_incompativel(None)


def test_parse_periodos() -> None:
    assert parse_periodos("Experiência 30+60") == ((30, 60), False)
    assert parse_periodos("exp 45 x 45") == ((45, 45), False)
    assert parse_periodos("experiencia 90+0") == ((90, 0), False)
    # Soma acima do teto legal -> premissa padrao.
    assert parse_periodos("experiencia 60+60") == ((45, 45), True)
    assert parse_periodos("Experiência") == ((45, 45), True)


def _prazo(admissao: date, tipo: str = "Experiência", today: date = TODAY):
    return compute_prazo(
        employee_id=1,
        nome_completo="Joao",
        cargo="Pedreiro",
        obra="Obra A",
        tipo_contrato=tipo,
        data_admissao=admissao,
        origem="tipo_contrato",
        today=today,
    )


def test_contagem_admissao_e_dia_um() -> None:
    p = _prazo(date(2026, 3, 1), today=date(2026, 3, 1))
    assert p.fim_primeiro_periodo == date(2026, 4, 14)  # 45o dia
    assert p.fim_experiencia == date(2026, 5, 29)  # 90o dia
    assert p.periodos_assumidos is True


def test_marco_passa_para_efetivacao_apos_primeiro_periodo() -> None:
    adm = date(2026, 3, 1)
    assert _prazo(adm, today=date(2026, 4, 14)).proximo_marco == MARCO_PRORROGACAO
    p = _prazo(adm, today=date(2026, 4, 15))
    assert p.proximo_marco == MARCO_EFETIVACAO
    assert p.dias_restantes == (date(2026, 5, 29) - date(2026, 4, 15)).days
    assert _prazo(adm, today=date(2026, 5, 30)).proximo_marco is None


def test_periodo_unico_so_tem_efetivacao() -> None:
    p = _prazo(date(2026, 9, 1), tipo="experiencia 90+0")
    assert p.proximo_marco == MARCO_EFETIVACAO


def test_recipients_env_com_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERIENCIA_ALERT_EMAILS", raising=False)
    monkeypatch.setenv("ASO_ALERT_EMAILS", "rh@primor.com")
    assert recipients_from_env() == ["rh@primor.com"]
    monkeypatch.setenv("EXPERIENCIA_ALERT_EMAILS", "a@x.com, b@x.com")
    assert recipients_from_env() == ["a@x.com", "b@x.com"]


# --- banco ------------------------------------------------------------------


async def _emp(
    db: AsyncSession,
    *,
    cpf: str,
    nome: str,
    admissao: date | None,
    tipo: str | None = "Experiência",
    status: str = "ativo",
) -> Employee:
    e = Employee(
        cpf=cpf,
        nome_completo=nome,
        cargo="Pedreiro",
        obra="Obra A",
        tipo_contrato=tipo,
        data_admissao=admissao,
        status=status,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


def _adm_para_marco_em(dias: int, periodo: int = 45) -> date:
    """Admissao cujo fim do periodo de `periodo` dias cai em TODAY + dias."""
    return TODAY + timedelta(days=dias) - timedelta(days=periodo - 1)


@pytest.mark.asyncio
async def test_list_prazos_filtra_e_ordena(db_session: AsyncSession) -> None:
    perto = await _emp(db_session, cpf="1", nome="Perto", admissao=_adm_para_marco_em(3))
    longe = await _emp(db_session, cpf="2", nome="Longe", admissao=_adm_para_marco_em(40))
    # CLT sem documento: nao entra.
    await _emp(db_session, cpf="3", nome="CLT", admissao=_adm_para_marco_em(3), tipo="CLT")
    # CLT com contrato de experiencia anexado: entra pela origem documento.
    doc = await _emp(db_session, cpf="4", nome="Doc", admissao=_adm_para_marco_em(10), tipo="CLT")
    db_session.add(EmployeeDocument(employee_id=doc.id, tipo=DOC_EMP_CONTRATO_EXPERIENCIA))
    # PJ com documento: tipo incompativel, nao entra.
    pj = await _emp(db_session, cpf="5", nome="PJ", admissao=_adm_para_marco_em(10), tipo="PJ")
    db_session.add(EmployeeDocument(employee_id=pj.id, tipo=DOC_EMP_CONTRATO_EXPERIENCIA))
    await db_session.commit()
    # Desligado / sem admissao / experiencia ja encerrada: nao entram.
    await _emp(db_session, cpf="6", nome="Desl", admissao=_adm_para_marco_em(3), status=STATUS_DESLIGADO)
    await _emp(db_session, cpf="7", nome="SemAdm", admissao=None)
    await _emp(db_session, cpf="8", nome="Velho", admissao=TODAY - timedelta(days=120))

    prazos = await list_prazos_experiencia(db_session, today=TODAY)
    assert [p.nome_completo for p in prazos] == ["Perto", "Doc", "Longe"]
    assert prazos[1].origem == "documento"
    assert prazos[0].employee_id == perto.id and prazos[0].dias_restantes == 3

    so_perto = await list_prazos_experiencia(db_session, today=TODAY, horizonte_dias=15)
    assert {p.employee_id for p in so_perto} == {perto.id, doc.id}
    assert longe.id not in {p.employee_id for p in so_perto}


@pytest.mark.asyncio
async def test_dispatch_cria_notificacao_e_e_idempotente(
    db_session: AsyncSession,
) -> None:
    alvo = await _emp(db_session, cpf="1", nome="Maria", admissao=_adm_para_marco_em(5))
    await _emp(db_session, cpf="2", nome="Fora", admissao=_adm_para_marco_em(30))
    recipients = ["RH@primor.com", "dp@primor.com"]

    s1 = await dispatch_experiencia_alerts(db_session, recipients=recipients, today=TODAY)
    assert (s1.sent, s1.skipped, s1.failed) == (1, 1, 0)
    notifs = list((await db_session.execute(select(Notificacao))).scalars())
    assert len(notifs) == 2
    n = notifs[0]
    assert n.categoria == CATEGORIA_DP
    assert "Maria" in n.titulo and "1º período" in n.titulo
    assert "45+45 assumida" in n.corpo
    assert n.link == f"/rh/funcionarios/{alvo.id}"
    assert {x.destinatario for x in notifs} == {"rh@primor.com", "dp@primor.com"}

    s2 = await dispatch_experiencia_alerts(db_session, recipients=recipients, today=TODAY)
    assert s2.sent == 0
    assert any(r.status == "skipped_already_sent" for r in s2.results)
    total = (await db_session.execute(select(func.count()).select_from(Notificacao))).scalar_one()
    assert total == 2

    # Janela seguinte (0d, dia do marco) gera aviso novo.
    marco = TODAY + timedelta(days=5)
    s3 = await dispatch_experiencia_alerts(db_session, recipients=recipients, today=marco)
    assert s3.sent == 1
    titulos = [x.titulo for x in (await db_session.execute(select(Notificacao))).scalars()]
    assert any("HOJE" in t for t in titulos)


@pytest.mark.asyncio
async def test_dispatch_sem_recipients(db_session: AsyncSession) -> None:
    s = await dispatch_experiencia_alerts(db_session, recipients=[], today=TODAY)
    assert s.total_employees == 0


# --- HTTP -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_prazos(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    today = date.today()
    await _emp(db_session, cpf="1", nome="Ana", admissao=today - timedelta(days=40))

    unauth = await api_client.get("/api/v1/dp-sesmt/experiencia/prazos")
    assert unauth.status_code == 401

    resp = await api_client.get(
        "/api/v1/dp-sesmt/experiencia/prazos", headers=auth_headers
    )
    assert resp.status_code == 200
    [item] = resp.json()
    assert item["nome_completo"] == "Ana"
    assert item["proximo_marco"] == MARCO_PRORROGACAO
    assert item["dias_restantes"] == 4
    assert item["periodos_assumidos"] is True

    resp2 = await api_client.get(
        "/api/v1/dp-sesmt/experiencia/prazos?horizonte_dias=2",
        headers=auth_headers,
    )
    assert resp2.json() == []


@pytest.mark.asyncio
async def test_endpoint_dispatch_manual(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXPERIENCIA_ALERT_EMAILS", raising=False)
    monkeypatch.delenv("ASO_ALERT_EMAILS", raising=False)
    resp = await api_client.post(
        "/api/v1/dp-sesmt/experiencia/alerts/dispatch", headers=auth_headers
    )
    assert resp.status_code == 422

    today = date.today()
    await _emp(db_session, cpf="1", nome="Ana", admissao=today - timedelta(days=40))
    monkeypatch.setenv("EXPERIENCIA_ALERT_EMAILS", "rh@primor.com")
    resp = await api_client.post(
        "/api/v1/dp-sesmt/experiencia/alerts/dispatch", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] == 1
