"""Afastamentos INSS (Demanda 4 - acompanhamento de afastados).

Cobre:

- CRUD do registro de afastamento (`Afastamento`) com `audit_log` em
  toda mutacao (LGPD/AGENTS.md). HTTP routers passam
  `actor=current_user.email`; workers/cron usam o default `system`.
- Cron diario `dispatch_afastamento_alerts` que dispara alertas para
  o RH (notificacao na plataforma sempre; e-mail pelo Microsoft 365
  quando configurado -- ver `notificacoes.alertas`) em duas categorias:
  - **DCB** (Data de Cessacao do Beneficio): janelas 30/15/7/0 dias.
    Se a empresa nao pedir prorrogacao antes do DCB, o INSS encerra
    o beneficio e o funcionario *deveria* voltar -- precisa de ASO
    de retorno, agendamento, etc.
  - **Pericia medica**: janelas 15/7/0 dias. Se o funcionario nao
    comparecer, o INSS suspende o pagamento.

Idempotencia: `AfastamentoAlertaLog` com unique
`(afastamento_id, kind, janela)` -- mesmo padrao do A.2/D.6.

Status canonico (`compute_*_status`):

- `dcb_status`: vigente | vencendo (<= 30 dias) | vencido | None.
- `pericia_status`: idem (15 dias de antecedencia).

Filtra `status == em_andamento` no SQL para nao alertar afastamentos
ja encerrados ou reabilitados.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as _date
from html import escape
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.core.config import get_settings
from app.integrations.msgraph_mail.client import GraphMailClient
from app.modules.dp_sesmt.models import (
    AFASTAMENTO_EM_ANDAMENTO,
    Afastamento,
    AfastamentoAlertaLog,
    Employee,
)
from app.modules.notificacoes.alertas import despachar_alerta
from app.modules.notificacoes.models import CATEGORIA_DP

logger = structlog.get_logger(__name__)

# Janelas de alerta. DCB usa as 4 janelas padrao do projeto (30/15/7/0
# - mesmas do ASO/CNDs). Pericia usa janelas mais apertadas porque o
# funcionario e quem precisa comparecer (nao da pra avisar com 30 dias).
JANELAS_DCB: tuple[int, ...] = (30, 15, 7, 0)
JANELAS_PERICIA: tuple[int, ...] = (15, 7, 0)
VENCENDO_DIAS_LIMITE = 30

# Audit log scaffolding (mesmo padrao dos outros modulos).
_AUDIT_RESOURCE = "dp_sesmt.afastamento"
_AUDIT_ACTOR_PLACEHOLDER = _AUDIT_ACTOR_SYSTEM


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str)
            if metadata
            else None,
        )
    )
    await db.commit()


# --- domain helpers --------------------------------------------------------


def compute_dcb_status(
    dcb: _date | None, *, today: _date | None = None
) -> str | None:
    if dcb is None:
        return None
    today = today or _date.today()
    delta = (dcb - today).days
    if delta < 0:
        return "vencido"
    if delta <= VENCENDO_DIAS_LIMITE:
        return "vencendo"
    return "vigente"


def compute_pericia_status(
    data_pericia: _date | None, *, today: _date | None = None
) -> str | None:
    if data_pericia is None:
        return None
    today = today or _date.today()
    delta = (data_pericia - today).days
    if delta < 0:
        return "vencido"
    if delta <= 15:
        return "vencendo"
    return "vigente"


def janela_for(
    target: _date | None,
    janelas: Sequence[int],
    *,
    today: _date | None = None,
) -> int | None:
    """Maior janela de alerta aplicavel hoje, ou None.

    Devolve None quando o target e None ou ja passou.
    """
    if target is None:
        return None
    today = today or _date.today()
    delta = (target - today).days
    if delta < 0:
        return None
    candidatas = [j for j in janelas if j >= delta]
    if not candidatas:
        return None
    return min(candidatas)


# --- CRUD ------------------------------------------------------------------


async def list_afastamentos(
    db: AsyncSession,
    *,
    employee_id: int | None = None,
    status: str | None = None,
) -> list[Afastamento]:
    stmt = select(Afastamento)
    if employee_id is not None:
        stmt = stmt.where(Afastamento.employee_id == employee_id)
    if status is not None:
        stmt = stmt.where(Afastamento.status == status)
    stmt = stmt.order_by(Afastamento.data_inicio.desc())
    return list((await db.execute(stmt)).scalars().all())


async def get_afastamento(
    db: AsyncSession, afastamento_id: int
) -> Afastamento | None:
    return await db.get(Afastamento, afastamento_id)


async def create_afastamento(
    db: AsyncSession,
    *,
    employee_id: int,
    beneficio_tipo: str,
    data_inicio: _date,
    numero_beneficio: str | None = None,
    cid: str | None = None,
    dcb: _date | None = None,
    data_pericia: _date | None = None,
    data_retorno: _date | None = None,
    status: str = AFASTAMENTO_EM_ANDAMENTO,
    observacoes: str | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> Afastamento | None:
    # Garante que o employee existe -- evitamos depender do FK puro
    # para devolver 404 com mensagem util ao inves de IntegrityError 500.
    if (await db.get(Employee, employee_id)) is None:
        return None

    row = Afastamento(
        employee_id=employee_id,
        beneficio_tipo=beneficio_tipo,
        numero_beneficio=numero_beneficio,
        cid=cid,
        data_inicio=data_inicio,
        dcb=dcb,
        data_pericia=data_pericia,
        data_retorno=data_retorno,
        status=status,
        observacoes=observacoes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _record_audit(
        db,
        action="create",
        resource_id=row.id,
        actor=actor,
        metadata={
            "employee_id": employee_id,
            "beneficio_tipo": beneficio_tipo,
            "data_inicio": data_inicio,
            "dcb": dcb,
        },
    )
    return row


async def update_afastamento(
    db: AsyncSession,
    afastamento_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
    **fields: object,
) -> Afastamento | None:
    row = await db.get(Afastamento, afastamento_id)
    if row is None:
        return None
    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if hasattr(row, key):
            old = getattr(row, key)
            if old != value:
                changed[key] = {"from": old, "to": value}
            setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    if changed:
        await _record_audit(
            db,
            action="update",
            resource_id=row.id,
            actor=actor,
            metadata={"changed": changed},
        )
    return row


async def delete_afastamento(
    db: AsyncSession,
    afastamento_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    row = await db.get(Afastamento, afastamento_id)
    if row is None:
        return False
    snapshot = {
        "employee_id": row.employee_id,
        "beneficio_tipo": row.beneficio_tipo,
        "data_inicio": row.data_inicio,
    }
    await db.delete(row)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource_id=afastamento_id,
        actor=actor,
        metadata=snapshot,
    )
    return True


# --- alerts ----------------------------------------------------------------


@dataclass(slots=True)
class AfastamentoAlertaResult:
    afastamento_id: int
    kind: str  # "dcb" | "pericia"
    janela: str
    status: str  # "sent" | "skipped_already_sent" | "skipped_no_data" | "failed"
    recipients: list[str]
    error_message: str | None = None
    email_status: str | None = None  # enviado | falhou | nao_configurado
    email_error: str | None = None


@dataclass(slots=True)
class AfastamentoAlertaSummary:
    total_afastamentos: int
    sent: int
    skipped: int
    failed: int
    results: list[AfastamentoAlertaResult] = field(default_factory=list)


@dataclass(slots=True)
class _AfastamentoSnapshot:
    """Snapshot detached -- mesmo motivo do `_EmployeeSnapshot` em
    `aso_alerts.py`: apos `db.rollback()` os ORM objects expiram e o
    lazy-refresh em `AsyncSession` falha com `MissingGreenlet`.
    """

    id: int
    employee_id: int
    employee_nome: str
    employee_cpf: str
    beneficio_tipo: str
    numero_beneficio: str | None
    dcb: _date | None
    data_pericia: _date | None


async def _get_existing_log(
    db: AsyncSession,
    afastamento_id: int,
    kind: str,
    janela: str,
) -> AfastamentoAlertaLog | None:
    stmt = (
        select(AfastamentoAlertaLog)
        .where(AfastamentoAlertaLog.afastamento_id == afastamento_id)
        .where(AfastamentoAlertaLog.kind == kind)
        .where(AfastamentoAlertaLog.janela == janela)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _render_alert_html(
    snapshot: _AfastamentoSnapshot,
    *,
    kind: str,
    janela: int,
    public_base_url: str,
    dias_restantes: int | None = None,
) -> str:
    dias = dias_restantes if dias_restantes is not None else janela
    target_label = "DCB" if kind == "dcb" else "Pericia medica"
    target_date = snapshot.dcb if kind == "dcb" else snapshot.data_pericia

    if janela == 0:
        urgencia = "VENCE hoje" if dias == 0 else f"Vence em {dias} dia(s)"
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

    nome = escape(snapshot.employee_nome or "")
    cpf = escape(snapshot.employee_cpf or "")
    beneficio = escape(snapshot.beneficio_tipo or "-")
    nb = escape(snapshot.numero_beneficio or "-")
    target_str = target_date.strftime("%d/%m/%Y") if target_date else "-"
    # Nao existe tela de detalhe por afastamento no web; a lista e o
    # destino (o link antigo `/rh/afastamentos/{id}` dava 404).
    dashboard_url = f"{public_base_url.rstrip('/')}/rh/afastamentos"

    return f"""
    <div style="font-family: Inter, Arial, sans-serif; color: #0f172a; max-width: 560px;">
      <h2 style="color: {cor}; margin-bottom: 8px;">{target_label} {urgencia}</h2>
      <p style="margin-top: 0; font-size: 14px;">
        Afastamento INSS do colaborador abaixo precisa de atencao.
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
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Tipo de beneficio</td>
            <td style="padding: 8px; font-size: 13px;">{beneficio}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Numero do beneficio</td>
            <td style="padding: 8px; font-size: 13px;">{nb}</td>
          </tr>
          <tr>
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">{target_label}</td>
            <td style="padding: 8px; font-size: 13px; color: {cor}; font-weight: 600;">{target_str}</td>
          </tr>
        </tbody>
      </table>
      <p style="margin-top: 20px; font-size: 13px;">
        <a href="{escape(dashboard_url)}" style="color: #2563eb; text-decoration: none;">
          Abrir Motor Central -> Afastamento ->
        </a>
      </p>
      <p style="margin-top: 24px; font-size: 11px; color: #94a3b8;">
        Voce esta recebendo este alerta porque foi cadastrado como destinatario
        de notificacoes de afastamentos INSS.
      </p>
    </div>
    """.strip()


async def _send_one_alert(
    db: AsyncSession,
    mailer: GraphMailClient | None,
    snapshot: _AfastamentoSnapshot,
    *,
    kind: str,
    janela: int,
    recipients: Sequence[str],
    public_base_url: str,
    today: _date,
) -> AfastamentoAlertaResult:
    janela_str = f"{janela}d"
    target = snapshot.dcb if kind == "dcb" else snapshot.data_pericia
    if target is None:
        return AfastamentoAlertaResult(
            afastamento_id=snapshot.id,
            kind=kind,
            janela=janela_str,
            status="skipped_no_data",
            recipients=list(recipients),
        )
    dias_restantes = (target - today).days
    existing = await _get_existing_log(db, snapshot.id, kind, janela_str)
    if existing is not None and existing.status == "sent":
        return AfastamentoAlertaResult(
            afastamento_id=snapshot.id,
            kind=kind,
            janela=janela_str,
            status="skipped_already_sent",
            recipients=list(recipients),
        )

    target_label = "DCB" if kind == "dcb" else "Pericia"
    titulo = (
        f"{target_label} de {snapshot.employee_nome or 'colaborador'} "
        f"em {dias_restantes} dia(s)"
    )
    html = _render_alert_html(
        snapshot,
        kind=kind,
        janela=janela,
        public_base_url=public_base_url,
        dias_restantes=dias_restantes,
    )
    # Sem CPF no corpo da notificacao (gravado por destinatario; o link
    # leva a tela de afastamentos) -- LGPD, minimo necessario.
    corpo = (
        f"Afastamento INSS de {snapshot.employee_nome or 'colaborador'} "
        f"({snapshot.beneficio_tipo or '-'}): "
        f"{'DCB' if kind == 'dcb' else 'pericia medica'} em "
        f"{target.strftime('%d/%m/%Y')}."
    )

    try:
        despacho = await despachar_alerta(
            db,
            recipients=recipients,
            categoria=CATEGORIA_DP,
            titulo=titulo,
            corpo=corpo,
            link="/rh/afastamentos",
            chave_idempotencia=f"afastamento:{snapshot.id}:{kind}:{janela_str}",
            email_subject=f"[INSS] {titulo}",
            email_html=html,
            mailer=mailer,
        )
        # Rele o log: `criar_notificacao` comita, e o objeto lido antes
        # pode estar stale.
        existing = await _get_existing_log(db, snapshot.id, kind, janela_str)
        log = existing or AfastamentoAlertaLog(
            afastamento_id=snapshot.id,
            kind=kind,
            janela=janela_str,
            recipients=list(recipients),
        )
        log.recipients = list(recipients)
        log.status = "sent"
        log.error_message = None
        log.email_status = despacho.email_status
        log.email_error = despacho.email_error
        if existing is None:
            db.add(log)
        await db.commit()
        return AfastamentoAlertaResult(
            afastamento_id=snapshot.id,
            kind=kind,
            janela=janela_str,
            status="sent",
            recipients=list(recipients),
            email_status=despacho.email_status,
            email_error=despacho.email_error,
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        # Re-fetch existing log fora da sessao expirada -- pode estar stale.
        existing = await _get_existing_log(db, snapshot.id, kind, janela_str)
        log = existing or AfastamentoAlertaLog(
            afastamento_id=snapshot.id,
            kind=kind,
            janela=janela_str,
            recipients=list(recipients),
        )
        log.recipients = list(recipients)
        log.status = "failed"
        log.error_message = str(exc)[:1024]
        if existing is None:
            db.add(log)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "afastamento_alerta_log_write_failed",
                afastamento_id=snapshot.id,
                kind=kind,
            )
        return AfastamentoAlertaResult(
            afastamento_id=snapshot.id,
            kind=kind,
            janela=janela_str,
            status="failed",
            recipients=list(recipients),
            error_message=str(exc)[:512],
        )


async def dispatch_afastamento_alerts(
    db: AsyncSession,
    *,
    recipients: Sequence[str],
    mailer: GraphMailClient | None = None,
    public_base_url: str | None = None,
    today: _date | None = None,
) -> AfastamentoAlertaSummary:
    """Itera afastamentos em andamento e dispara alertas de DCB / pericia.

    Filtra `status == em_andamento` no SQL. Para cada afastamento:
    - Calcula janela ativa de DCB e de pericia (se aplicavel).
    - Pula se ja foi despachado para essa (afastamento, kind, janela).
    - Cria notificacao na plataforma (sempre) + e-mail pelo Microsoft
      365 quando `mailer` vier configurado, e grava log.

    Falhas isoladas: 1 despacho com erro nao aborta os demais.
    """
    settings = get_settings()
    public_base_url = public_base_url or settings.public_base_url
    today = today or _date.today()

    if not recipients:
        logger.warning(
            "dispatch_afastamento_alerts_recipients_empty",
        )
        return AfastamentoAlertaSummary(0, 0, 0, 0, [])

    stmt = (
        select(
            Afastamento.id,
            Afastamento.employee_id,
            Afastamento.beneficio_tipo,
            Afastamento.numero_beneficio,
            Afastamento.dcb,
            Afastamento.data_pericia,
            Employee.nome_completo,
            Employee.cpf,
        )
        .join(Employee, Employee.id == Afastamento.employee_id)
        .where(Afastamento.status == AFASTAMENTO_EM_ANDAMENTO)
    )
    rows = (await db.execute(stmt)).all()
    snapshots = [
        _AfastamentoSnapshot(
            id=r.id,
            employee_id=r.employee_id,
            employee_nome=r.nome_completo,
            employee_cpf=r.cpf,
            beneficio_tipo=r.beneficio_tipo,
            numero_beneficio=r.numero_beneficio,
            dcb=r.dcb,
            data_pericia=r.data_pericia,
        )
        for r in rows
    ]

    sent = 0
    skipped = 0
    failed = 0
    results: list[AfastamentoAlertaResult] = []

    for snap in snapshots:
        # DCB
        janela = janela_for(snap.dcb, JANELAS_DCB, today=today)
        if janela is not None:
            r = await _send_one_alert(
                db,
                mailer,
                snap,
                kind="dcb",
                janela=janela,
                recipients=recipients,
                public_base_url=public_base_url,
                today=today,
            )
            results.append(r)
            if r.status == "sent":
                sent += 1
            elif r.status == "failed":
                failed += 1
            else:
                skipped += 1

        # Pericia
        janela = janela_for(snap.data_pericia, JANELAS_PERICIA, today=today)
        if janela is not None:
            r = await _send_one_alert(
                db,
                mailer,
                snap,
                kind="pericia",
                janela=janela,
                recipients=recipients,
                public_base_url=public_base_url,
                today=today,
            )
            results.append(r)
            if r.status == "sent":
                sent += 1
            elif r.status == "failed":
                failed += 1
            else:
                skipped += 1

    return AfastamentoAlertaSummary(
        total_afastamentos=len(snapshots),
        sent=sent,
        skipped=skipped,
        failed=failed,
        results=results,
    )
