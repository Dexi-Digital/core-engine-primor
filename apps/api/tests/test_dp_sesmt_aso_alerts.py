"""Testes do A.2 - alertas de vencimento de ASO.

Mesma fórmula do D.6 (certidoes) mas para o ASO de funcionarios. Os
testes garantem:

- `compute_aso_status` / `janela_for_aso` cobrem todos os casos.
- Dispatch envia em janela ativa, pula no-window/sem-validade.
- Idempotencia: 2 dispatches no mesmo dia mandam 1 email.
- Apenas funcionarios `ativo` recebem alerta (afastado / desligado nao).
- Subject/corpo usam `dias_restantes` reais (nao a janela).
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.resend.client import ResendClient
from app.modules.dp_sesmt.aso_alerts import (
    JANELAS_ALERTA,
    compute_aso_status,
    dispatch_aso_alerts,
    janela_for_aso,
    render_aso_alerta_html,
)
from app.modules.dp_sesmt.models import (
    STATUS_AFASTADO,
    STATUS_ATIVO,
    STATUS_DESLIGADO,
    Employee,
    EmployeeAsoAlertaLog,
)

# --- pure helpers -----------------------------------------------------------


def test_compute_aso_status_sem_validade() -> None:
    assert compute_aso_status(None) == "sem_validade"


def test_compute_aso_status_buckets() -> None:
    today = date(2026, 4, 25)
    assert (
        compute_aso_status(today - timedelta(days=1), today=today) == "vencido"
    )
    assert compute_aso_status(today, today=today) == "vencendo"
    assert (
        compute_aso_status(today + timedelta(days=30), today=today) == "vencendo"
    )
    assert (
        compute_aso_status(today + timedelta(days=31), today=today) == "vigente"
    )


def test_janela_for_aso_picks_smallest_active() -> None:
    today = date(2026, 4, 25)
    # 12 dias -> primeira janela >= 12 e 15
    assert janela_for_aso(today + timedelta(days=12), today=today) == 15
    # exatamente em uma janela
    assert janela_for_aso(today + timedelta(days=7), today=today) == 7
    # ja vencido
    assert janela_for_aso(today - timedelta(days=1), today=today) is None
    # > 30d (fora de janela)
    assert janela_for_aso(today + timedelta(days=45), today=today) is None
    # sem validade
    assert janela_for_aso(None, today=today) is None


def test_render_uses_real_days_not_window() -> None:
    today = date(2026, 4, 25)
    emp = Employee(
        id=1,
        cpf="11144477735",
        nome_completo="Joao da Silva",
        cargo="Pedreiro",
        obra="Obra A",
        status=STATUS_ATIVO,
        aso_validade=today + timedelta(days=12),  # cai na janela 15d
    )
    html = render_aso_alerta_html(
        emp,
        janela=15,
        public_base_url="https://primor.example",
        dias_restantes=12,
    )
    assert "12 dia" in html
    # nao pode usar a janela como contagem real
    assert "15 dia" not in html


def test_janelas_alerta_includes_zero() -> None:
    """Sanity: JANELAS_ALERTA deve cobrir o dia do vencimento."""
    assert 0 in JANELAS_ALERTA


# --- dispatch (mock Resend) -------------------------------------------------


def _mock_resend(
    captured: list[httpx.Request], message_id: str = "msg_aso_xyz"
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
    cpf: str,
    nome: str = "Joao da Silva",
    aso_validade: date | None,
    status: str = STATUS_ATIVO,
) -> Employee:
    emp = Employee(
        cpf=cpf,
        nome_completo=nome,
        cargo="Pedreiro",
        obra="Obra A",
        status=status,
        aso_validade=aso_validade,
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


@pytest.mark.asyncio
async def test_dispatch_sends_for_employee_in_window(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    await _create_employee(
        db_session,
        cpf="11144477735",
        aso_validade=today + timedelta(days=10),  # cai na janela 15
    )

    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_aso_alerts(
        db_session, resend, recipients=["sesmt@primor.example"], today=today
    )
    await resend.aclose()

    assert summary.sent == 1
    assert summary.failed == 0
    assert len(captured) == 1
    body = captured[0].read().decode()
    # Subject usa dias REAIS (10), nao a janela (15) -- regressao ja
    # documentada no D.6 e replicada aqui.
    assert "10 dia" in body.lower()
    assert "vence em 15 dia" not in body.lower()
    # Log gravado
    log = (
        await db_session.execute(EmployeeAsoAlertaLog.__table__.select())
    ).first()
    assert log is not None


@pytest.mark.asyncio
async def test_dispatch_idempotent_same_window(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    await _create_employee(
        db_session,
        cpf="11144477735",
        aso_validade=today + timedelta(days=5),  # janela 7
    )

    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    s1 = await dispatch_aso_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    s2 = await dispatch_aso_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()

    assert s1.sent == 1
    assert s2.sent == 0
    assert s2.skipped == 1
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_status_nao_ativo(
    db_session: AsyncSession,
) -> None:
    """Afastados / desligados nao recebem alerta de ASO -- quando
    voltarem, fazem ASO de retorno ao trabalho. Filtro feito no SQL."""
    today = date(2026, 4, 25)
    await _create_employee(
        db_session,
        cpf="11144477735",
        nome="Funcionario Afastado",
        aso_validade=today + timedelta(days=5),
        status=STATUS_AFASTADO,
    )
    await _create_employee(
        db_session,
        cpf="11144477736",
        nome="Funcionario Desligado",
        aso_validade=today + timedelta(days=5),
        status=STATUS_DESLIGADO,
    )

    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_aso_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()
    assert summary.sent == 0
    assert summary.total_employees == 0  # SQL filtrou
    assert len(captured) == 0


@pytest.mark.asyncio
async def test_dispatch_skips_no_validade(db_session: AsyncSession) -> None:
    today = date(2026, 4, 25)
    await _create_employee(
        db_session, cpf="11144477735", aso_validade=None
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_aso_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()
    # employee ativo mas sem validade -- filtro `aso_validade IS NOT NULL`
    # ja exclui no SQL, entao total_employees = 0 (consistente com
    # `test_dispatch_skips_status_nao_ativo`).
    assert summary.sent == 0
    assert summary.total_employees == 0
    assert len(captured) == 0


@pytest.mark.asyncio
async def test_dispatch_continues_after_db_rollback_in_loop(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressao do bug do Devin Review: quando `db.commit()` falha
    no meio do loop e dispara `db.rollback()`, SQLAlchemy 2.0 expira
    TODOS os ORM objects da sessao. O AsyncSession nao suporta
    lazy-refresh implicito (sem greenlet), entao o proximo acesso a
    `employee.aso_validade` crashava com `MissingGreenlet` e abortava
    o resto do dispatch.

    Fix: snapshot dos campos do Employee em dataclass puro
    (`_EmployeeSnapshot`) antes do loop, isolando a iteracao do
    estado da sessao.

    Setup: 2 funcionarios elegiveis. Patch em `db.commit` para falhar
    APENAS no primeiro `commit()` -- forca o branch de erro do dispatch,
    que chama `db.rollback()`. Sem o fix, o segundo employee causaria
    `MissingGreenlet` ao acessar `aso_validade`. Com o fix, o loop
    continua e o segundo employee e processado normalmente.
    """
    today = date(2026, 4, 25)
    emp1 = await _create_employee(
        db_session,
        cpf="11144477735",
        nome="Funcionario A",
        aso_validade=today + timedelta(days=10),  # janela 15
    )
    emp2 = await _create_employee(
        db_session,
        cpf="22255588846",
        nome="Funcionario B",
        aso_validade=today + timedelta(days=5),  # janela 7
    )
    # Capturamos os ids ANTES do dispatch -- depois do rollback no meio
    # do loop, esses ORM objects ficam expirados e qualquer acesso aqui
    # tambem dispararia MissingGreenlet.
    emp1_id = emp1.id
    emp2_id = emp2.id

    # Resend OK para todo mundo -- a falha sera no commit, nao no envio.
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)

    original_commit = db_session.commit
    fail_count = {"remaining": 1}

    async def flaky_commit() -> None:
        if fail_count["remaining"] > 0:
            fail_count["remaining"] -= 1
            raise RuntimeError("simulated commit failure")
        await original_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    summary = await dispatch_aso_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()

    # Sem o fix: summary.failed=1, summary.sent=0 e o segundo employee
    # NAO aparece em results (perdido por MissingGreenlet).
    # Com o fix: o loop continua. O 1o tem commit falho (`status=failed`,
    # commit retornou erro); o 2o passa normal (`sent`).
    assert summary.total_employees == 2
    employee_ids_in_results = {r.employee_id for r in summary.results}
    assert employee_ids_in_results == {emp1_id, emp2_id}, (
        "ambos os funcionarios devem aparecer em results -- segundo "
        "nao pode ser perdido por MissingGreenlet apos rollback"
    )


@pytest.mark.asyncio
async def test_dispatch_empty_recipients_warns_and_returns(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    await _create_employee(
        db_session, cpf="11144477735", aso_validade=today + timedelta(days=5)
    )
    captured: list[httpx.Request] = []
    resend = _mock_resend(captured)
    summary = await dispatch_aso_alerts(
        db_session, resend, recipients=[], today=today
    )
    await resend.aclose()
    assert summary.sent == 0
    assert summary.total_employees == 0
    assert len(captured) == 0
