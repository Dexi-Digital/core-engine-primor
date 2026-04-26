"""Alertas de vencimento de ASO (A.2).

Espelho funcional do `app/modules/licitacoes/certidoes.py` mas para
funcionarios -- mesmo conjunto de janelas (30/15/7/0) e idempotencia
via `EmployeeAsoAlertaLog`.

Diferencas importantes em relacao a certidoes:

- Considera so funcionarios com `status == "ativo"`. Desligados nao
  precisam de ASO renovado. Afastados (INSS) tambem nao -- quando
  voltarem, fazem novo ASO de retorno ao trabalho. Filtro feito em SQL
  para nao iterar lista grande in-memory.

- O destinatario padrao e a env `ASO_ALERT_EMAILS` (lista CSV). Cron
  pula silenciosamente se vazia (em dev a Primor nao tem que receber
  email de teste).

- Mensagem do email cita o cargo + obra do funcionario para ajudar o
  RH a identificar de quem e o ASO sem precisar abrir a UI.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from html import escape

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.resend.client import ResendClient, ResendError
from app.modules.dp_sesmt.models import (
    STATUS_ATIVO,
    Employee,
    EmployeeAsoAlertaLog,
)

logger = structlog.get_logger(__name__)

# Janelas (em dias antes do vencimento). Mesmas do D.6 -- mantemos o
# mesmo conjunto para consistencia operacional do RH.
JANELAS_ALERTA: tuple[int, ...] = (30, 15, 7, 0)
VENCENDO_DIAS_LIMITE = 30


# --- domain helpers --------------------------------------------------------


def compute_aso_status(
    aso_validade: _date | None, *, today: _date | None = None
) -> str:
    """Status textual para UI/filtros.

    - `sem_validade`: nao existe data de validade no cadastro.
    - `vencido`: today > validade.
    - `vencendo`: faltam <= 30 dias.
    - `vigente`: caso contrario.
    """
    if aso_validade is None:
        return "sem_validade"
    today = today or _date.today()
    delta = (aso_validade - today).days
    if delta < 0:
        return "vencido"
    if delta <= VENCENDO_DIAS_LIMITE:
        return "vencendo"
    return "vigente"


def janela_for_aso(
    aso_validade: _date | None, *, today: _date | None = None
) -> int | None:
    """Maior janela de alerta aplicavel hoje, ou None.

    Mesma logica do `janela_for_certidao`. Devolve None quando nao
    existe validade ou ja venceu (alerta pos-vencimento e fora do
    escopo deste modulo).
    """
    if aso_validade is None:
        return None
    today = today or _date.today()
    delta = (aso_validade - today).days
    if delta < 0:
        return None
    candidatas = [j for j in JANELAS_ALERTA if j >= delta]
    if not candidatas:
        return None
    return min(candidatas)


# --- alert dispatcher -----------------------------------------------------


@dataclass(slots=True)
class AsoAlertaResult:
    employee_id: int
    janela: str
    status: str
    recipients: list[str]
    resend_message_id: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class AsoAlertaSummary:
    total_employees: int
    sent: int
    skipped: int
    failed: int
    results: list[AsoAlertaResult]


@dataclass(slots=True)
class _EmployeeSnapshot:
    """Snapshot dos campos do Employee usados no dispatch.

    Existe para isolar a iteracao do estado da sessao SQLAlchemy: apos
    um `db.rollback()` (per-employee error path) o SQLAlchemy 2.0 expira
    TODOS os ORM objects da sessao, e o lazy-refresh em `AsyncSession`
    falha com `MissingGreenlet`. Carregando os campos uma vez e
    iterando sobre dataclasses, isolamos o loop dessa armadilha.
    """

    id: int
    nome_completo: str
    cpf: str
    cargo: str | None
    obra: str | None
    aso_validade: _date  # nao-None: filtramos no SQL upstream


async def _alerta_already_sent(
    db: AsyncSession, employee_id: int, janela: str
) -> bool:
    stmt = (
        select(EmployeeAsoAlertaLog.id)
        .where(EmployeeAsoAlertaLog.employee_id == employee_id)
        .where(EmployeeAsoAlertaLog.janela == janela)
        .where(EmployeeAsoAlertaLog.status == "sent")
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _get_existing_log(
    db: AsyncSession, employee_id: int, janela: str
) -> EmployeeAsoAlertaLog | None:
    stmt = (
        select(EmployeeAsoAlertaLog)
        .where(EmployeeAsoAlertaLog.employee_id == employee_id)
        .where(EmployeeAsoAlertaLog.janela == janela)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def render_aso_alerta_html(
    employee: Employee | _EmployeeSnapshot,
    *,
    janela: int,
    public_base_url: str,
    dias_restantes: int | None = None,
) -> str:
    """Render do email de alerta de ASO.

    Igual ao render de certidoes: a `janela` define a *cor / urgencia*
    e `dias_restantes` exibe a contagem real ate o vencimento (em
    licitacao/SESMT essa diferenca importa: ASO com 12 dias cai na
    janela 15, mas o email precisa dizer "12 dias", nao "15").
    """
    dias = dias_restantes if dias_restantes is not None else janela
    if janela == 0:
        urgencia = "VENCIDO hoje" if dias == 0 else f"Vence em {dias} dia(s)"
        cor = "#dc2626"
    elif janela <= 7:
        urgencia = f"Vence em {dias} dia(s)"
        cor = "#ea580c"
    elif janela <= 15:
        urgencia = f"Vence em {dias} dia(s)"
        cor = "#d97706"
    else:
        urgencia = f"Vence em {dias} dia(s)"
        cor = "#0ea5e9"

    nome = escape(employee.nome_completo or "")
    cargo = escape(employee.cargo or "-")
    obra = escape(employee.obra or "-")
    cpf = escape(employee.cpf or "")
    validade_str = (
        employee.aso_validade.strftime("%d/%m/%Y")
        if employee.aso_validade
        else "-"
    )
    dashboard_url = (
        f"{public_base_url.rstrip('/')}/rh/funcionarios/{employee.id}"
    )

    return f"""
    <div style="font-family: Inter, Arial, sans-serif; color: #0f172a; max-width: 560px;">
      <h2 style="color: {cor}; margin-bottom: 8px;">ASO {urgencia}</h2>
      <p style="margin-top: 0; font-size: 14px;">
        O atestado de saude ocupacional do colaborador abaixo precisa de atencao.
      </p>
      <table style="width: 100%; border-collapse: collapse; margin-top: 16px;">
        <tbody>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc; width: 30%;">Funcionario</td>
            <td style="padding: 8px; font-size: 13px;">{nome}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">CPF</td>
            <td style="padding: 8px; font-size: 13px;">{cpf}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Cargo</td>
            <td style="padding: 8px; font-size: 13px;">{cargo}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Obra</td>
            <td style="padding: 8px; font-size: 13px;">{obra}</td>
          </tr>
          <tr>
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Validade do ASO</td>
            <td style="padding: 8px; font-size: 13px; color: {cor}; font-weight: 600;">{validade_str}</td>
          </tr>
        </tbody>
      </table>
      <p style="margin-top: 20px; font-size: 13px;">
        <a href="{escape(dashboard_url)}" style="color: #2563eb; text-decoration: none;">
          Abrir Motor Central -> Funcionario ->
        </a>
      </p>
      <p style="margin-top: 24px; font-size: 11px; color: #94a3b8;">
        Voce esta recebendo este alerta porque foi cadastrado como destinatario
        de notificacoes de ASO. Janelas: 30, 15, 7 e 0 dias antes do vencimento.
      </p>
    </div>
    """.strip()


async def dispatch_aso_alerts(
    db: AsyncSession,
    resend: ResendClient,
    *,
    recipients: Sequence[str],
    public_base_url: str | None = None,
    from_email: str | None = None,
    today: _date | None = None,
) -> AsoAlertaSummary:
    """Itera funcionarios ativos e dispara alertas de ASO vencendo.

    Filtra `status == ativo` ja no SQL para nao trazer lista enorme
    inutilmente. Por funcionario:
    - Calcula janela ativa (None se sem validade ou ja vencido).
    - Pula se ja foi enviado para essa (employee, janela).
    - Envia email + grava log.

    Falhas isoladas: 1 envio com erro nao aborta os demais.
    """
    settings = get_settings()
    public_base_url = public_base_url or settings.public_base_url
    from_email = from_email or settings.resend_from_email
    today = today or _date.today()

    if not recipients:
        logger.warning("dispatch_aso_alerts: lista de recipients vazia")
        return AsoAlertaSummary(0, 0, 0, 0, [])

    stmt = (
        select(
            Employee.id,
            Employee.nome_completo,
            Employee.cpf,
            Employee.cargo,
            Employee.obra,
            Employee.aso_validade,
        )
        .where(Employee.status == STATUS_ATIVO)
        .where(Employee.aso_validade.is_not(None))
    )
    rows = (await db.execute(stmt)).all()
    # Snapshot detached -- iteramos sobre dataclasses puras para que um
    # rollback no meio do loop (que expira ORM objects) nao quebre os
    # acessos subsequentes a `aso_validade`/`nome_completo` etc.
    employees: list[_EmployeeSnapshot] = [
        _EmployeeSnapshot(
            id=r.id,
            nome_completo=r.nome_completo,
            cpf=r.cpf,
            cargo=r.cargo,
            obra=r.obra,
            aso_validade=r.aso_validade,
        )
        for r in rows
    ]

    sent = 0
    skipped = 0
    failed = 0
    results: list[AsoAlertaResult] = []

    for employee in employees:
        janela = janela_for_aso(employee.aso_validade, today=today)
        if janela is None:
            skipped += 1
            results.append(
                AsoAlertaResult(
                    employee_id=employee.id,
                    janela="none",
                    status="skipped_no_window",
                    recipients=list(recipients),
                )
            )
            continue

        janela_str = f"{janela}d"
        if await _alerta_already_sent(db, employee.id, janela_str):
            skipped += 1
            results.append(
                AsoAlertaResult(
                    employee_id=employee.id,
                    janela=janela_str,
                    status="skipped_already_sent",
                    recipients=list(recipients),
                )
            )
            continue

        assert employee.aso_validade is not None  # janela_for_aso filtrou
        dias_restantes = (employee.aso_validade - today).days

        html = render_aso_alerta_html(
            employee,
            janela=janela,
            public_base_url=public_base_url,
            dias_restantes=dias_restantes,
        )
        validade_str = employee.aso_validade.strftime("%d/%m/%Y")
        if dias_restantes == 0:
            subject = (
                f"[Motor Central] ASO VENCIDO hoje: "
                f"{employee.nome_completo} ({validade_str})"
            )
        else:
            subject = (
                f"[Motor Central] ASO vence em {dias_restantes} dia(s): "
                f"{employee.nome_completo} ({validade_str})"
            )

        existing_log = await _get_existing_log(db, employee.id, janela_str)

        try:
            resp = await resend.send_email(
                to=list(recipients),
                subject=subject,
                html=html,
                from_=from_email,
            )
        except (ResendError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "alerta aso falhou",
                employee_id=employee.id,
                janela=janela_str,
                error=str(exc),
            )
            error_msg = str(exc)[:1024]
            if existing_log is None:
                db.add(
                    EmployeeAsoAlertaLog(
                        employee_id=employee.id,
                        janela=janela_str,
                        recipients=list(recipients),
                        status="failed",
                        error_message=error_msg,
                    )
                )
            else:
                existing_log.recipients = list(recipients)
                existing_log.status = "failed"
                existing_log.error_message = error_msg
            try:
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "falha ao gravar log de erro do alerta aso",
                    employee_id=employee.id,
                    janela=janela_str,
                )
                await db.rollback()
            failed += 1
            results.append(
                AsoAlertaResult(
                    employee_id=employee.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message=error_msg,
                )
            )
            continue

        message_id = resp.get("id") if isinstance(resp, dict) else None
        if existing_log is None:
            db.add(
                EmployeeAsoAlertaLog(
                    employee_id=employee.id,
                    janela=janela_str,
                    recipients=list(recipients),
                    resend_message_id=message_id,
                    status="sent",
                )
            )
        else:
            existing_log.recipients = list(recipients)
            existing_log.resend_message_id = message_id
            existing_log.status = "sent"
            existing_log.error_message = None
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "falha ao gravar log de sucesso do alerta aso",
                employee_id=employee.id,
                janela=janela_str,
            )
            await db.rollback()
            failed += 1
            results.append(
                AsoAlertaResult(
                    employee_id=employee.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message="db_commit_failed",
                )
            )
            continue
        sent += 1
        results.append(
            AsoAlertaResult(
                employee_id=employee.id,
                janela=janela_str,
                status="sent",
                recipients=list(recipients),
                resend_message_id=message_id,
            )
        )

    return AsoAlertaSummary(
        total_employees=len(employees),
        sent=sent,
        skipped=skipped,
        failed=failed,
        results=results,
    )


__all__ = [
    "AsoAlertaResult",
    "AsoAlertaSummary",
    "JANELAS_ALERTA",
    "VENCENDO_DIAS_LIMITE",
    "compute_aso_status",
    "dispatch_aso_alerts",
    "janela_for_aso",
    "render_aso_alerta_html",
]
