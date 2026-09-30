"""Alertas de vencimento de contratos (Squad 5).

Copia deliberada do desenho de `licitacoes/certidoes.py` (D.6):
janelas 30/15/7/0 dias, log idempotente por (contrato_id, janela),
commit per-item, retry in-place de despachos failed. Canal:
notificacao na plataforma sempre + e-mail pelo Microsoft 365 quando
configurado (`app.modules.notificacoes.alertas`). Diferencas:

- So contratos `vigente` ou `judicializado` alertam; `rascunho` e
  `encerrado` sao skipped.
- Contrato sem `data_fim` (prazo indeterminado) e skipped.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.msgraph_mail.client import GraphMailClient
from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO,
    Contrato,
    ContratoAlertaLog,
)
from app.modules.licitacoes.certidoes import janela_for_certidao
from app.modules.notificacoes.alertas import despachar_alerta
from app.modules.notificacoes.models import CATEGORIA_FINANCEIRO

logger = logging.getLogger(__name__)

# Status que geram alerta. Judicializado continua alertando: vencimento
# de contrato em juizo e justamente o que o financeiro nao pode perder.
_STATUS_ALERTAVEIS = frozenset({"vigente", "judicializado"})


@dataclass(slots=True)
class AlertaResult:
    contrato_id: int
    janela: str
    status: str  # sent | skipped_status | skipped_no_data_fim | skipped_already_sent | failed
    recipients: list[str]
    error_message: str | None = None
    email_status: str | None = None  # enviado | falhou | nao_configurado
    email_error: str | None = None


@dataclass(slots=True)
class AlertaSummary:
    total_contratos: int
    sent: int
    skipped: int
    failed: int
    results: list[AlertaResult]


@dataclass(slots=True)
class _ContratoSnapshot:
    """Snapshot detached -- mesmo racional do `_CertidaoSnapshot` do D.6
    (rollback per-item expira ORM objects; lazy-refresh em AsyncSession
    estoura MissingGreenlet)."""

    id: int
    titulo: str
    contraparte_nome: str
    tipo: str
    status: str
    data_fim: _date | None


def render_alerta_contrato_html(
    contrato: Contrato | _ContratoSnapshot,
    *,
    janela: int,
    public_base_url: str,
    dias_restantes: int | None = None,
) -> str:
    """Email de alerta. `janela` da a urgencia/cor; `dias_restantes` e o
    numero real exibido (mesma distincao do D.6)."""
    status_label = dict(STATUS_CONTRATO).get(contrato.status, contrato.status)
    dias = dias_restantes if dias_restantes is not None else janela
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
        cor = "#0284c7"
    data_fim_str = (
        contrato.data_fim.strftime("%d/%m/%Y") if contrato.data_fim else "-"
    )
    dashboard_url = f"{public_base_url.rstrip('/')}/financeiro/contratos"
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 640px; margin: 0 auto; color: #0f172a;">
      <h2 style="margin: 0 0 4px;">Contrato com vencimento proximo</h2>
      <p style="margin: 0 0 16px; color: {cor}; font-weight: 600; font-size: 14px;">
        {escape(urgencia)}
      </p>
      <table style="width: 100%; border-collapse: collapse; border: 1px solid #e5e7eb;">
        <tbody>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc; width: 30%;">Contrato</td>
            <td style="padding: 8px; font-size: 13px;">{escape(contrato.titulo)}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Contraparte</td>
            <td style="padding: 8px; font-size: 13px;">{escape(contrato.contraparte_nome)}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Status</td>
            <td style="padding: 8px; font-size: 13px;">{escape(status_label)}</td>
          </tr>
          <tr>
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Data fim</td>
            <td style="padding: 8px; font-size: 13px; color: {cor}; font-weight: 600;">{escape(data_fim_str)}</td>
          </tr>
        </tbody>
      </table>
      <p style="margin-top: 20px; font-size: 13px;">
        <a href="{escape(dashboard_url)}" style="color: #2563eb; text-decoration: none;">
          Abrir Motor Central -> Contratos ->
        </a>
      </p>
      <p style="margin-top: 24px; font-size: 11px; color: #94a3b8;">
        Alerta automatico de vencimento de contrato. Janelas: 30, 15, 7 e 0
        dias antes da data fim.
      </p>
    </div>
    """.strip()


async def _alerta_already_sent(
    db: AsyncSession, contrato_id: int, janela: str
) -> bool:
    stmt = (
        select(ContratoAlertaLog.id)
        .where(ContratoAlertaLog.contrato_id == contrato_id)
        .where(ContratoAlertaLog.janela == janela)
        .where(ContratoAlertaLog.status == "sent")
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _get_existing_log(
    db: AsyncSession, contrato_id: int, janela: str
) -> ContratoAlertaLog | None:
    stmt = (
        select(ContratoAlertaLog)
        .where(ContratoAlertaLog.contrato_id == contrato_id)
        .where(ContratoAlertaLog.janela == janela)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def dispatch_contrato_alerts(
    db: AsyncSession,
    *,
    recipients: Sequence[str],
    mailer: GraphMailClient | None = None,
    public_base_url: str | None = None,
    today: _date | None = None,
) -> AlertaSummary:
    """Dispara alertas de vencimento de contratos (paridade com o
    dispatch_expiration_alerts do D.6 -- ver docstring do modulo).

    Notificacao na plataforma sempre; e-mail pelo Microsoft 365 so com
    `mailer` configurado. O alerta conta como despachado assim que a
    notificacao existe (ver `notificacoes.alertas`)."""
    settings = get_settings()
    public_base_url = public_base_url or settings.public_base_url
    today = today or _date.today()

    if not recipients:
        logger.warning("dispatch_contrato_alerts: lista de recipients vazia")
        return AlertaSummary(0, 0, 0, 0, [])

    stmt = select(
        Contrato.id,
        Contrato.titulo,
        Contrato.contraparte_nome,
        Contrato.tipo,
        Contrato.status,
        Contrato.data_fim,
    )
    rows = (await db.execute(stmt)).all()
    contratos = [
        _ContratoSnapshot(
            id=r.id,
            titulo=r.titulo,
            contraparte_nome=r.contraparte_nome,
            tipo=r.tipo,
            status=r.status,
            data_fim=r.data_fim,
        )
        for r in rows
    ]

    sent = 0
    skipped = 0
    failed = 0
    results: list[AlertaResult] = []

    for contrato in contratos:
        if contrato.status not in _STATUS_ALERTAVEIS:
            skipped += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela="none",
                    status="skipped_status",
                    recipients=list(recipients),
                )
            )
            continue
        janela = janela_for_certidao(contrato.data_fim, today=today)
        if janela is None:
            skipped += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela="none",
                    status="skipped_no_data_fim",
                    recipients=list(recipients),
                )
            )
            continue

        janela_str = f"{janela}d"
        if await _alerta_already_sent(db, contrato.id, janela_str):
            skipped += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    status="skipped_already_sent",
                    recipients=list(recipients),
                )
            )
            continue

        assert contrato.data_fim is not None  # janela filtrou None
        dias_restantes = (contrato.data_fim - today).days
        html = render_alerta_contrato_html(
            contrato,
            janela=janela,
            public_base_url=public_base_url,
            dias_restantes=dias_restantes,
        )
        data_fim_str = contrato.data_fim.strftime("%d/%m/%Y")
        if dias_restantes == 0:
            titulo = f"Contrato VENCE hoje: {contrato.titulo} ({data_fim_str})"
        else:
            titulo = (
                f"Contrato vence em {dias_restantes} dia(s): "
                f"{contrato.titulo} ({data_fim_str})"
            )
        status_label = dict(STATUS_CONTRATO).get(contrato.status, contrato.status)
        corpo = (
            f"{contrato.titulo} -- contraparte {contrato.contraparte_nome}, "
            f"status {status_label}. Data fim: {data_fim_str}."
        )

        try:
            despacho = await despachar_alerta(
                db,
                recipients=recipients,
                categoria=CATEGORIA_FINANCEIRO,
                titulo=titulo,
                corpo=corpo,
                link="/financeiro/contratos",
                chave_idempotencia=f"contrato:{contrato.id}:{janela_str}",
                email_subject=f"[Motor Central] {titulo}",
                email_html=html,
                mailer=mailer,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "alerta contrato=%s janela=%s falhou: %s",
                contrato.id,
                janela_str,
                exc,
                exc_info=False,
            )
            error_msg = str(exc)[:1024]
            await db.rollback()
            existing_log = await _get_existing_log(db, contrato.id, janela_str)
            if existing_log is None:
                db.add(
                    ContratoAlertaLog(
                        contrato_id=contrato.id,
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
                    "falha ao gravar log de erro do alerta contrato=%s janela=%s",
                    contrato.id,
                    janela_str,
                )
                await db.rollback()
            failed += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message=error_msg,
                )
            )
            continue

        existing_log = await _get_existing_log(db, contrato.id, janela_str)
        if existing_log is None:
            db.add(
                ContratoAlertaLog(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    recipients=list(recipients),
                    status="sent",
                    email_status=despacho.email_status,
                    email_error=despacho.email_error,
                )
            )
        else:
            existing_log.recipients = list(recipients)
            existing_log.status = "sent"
            existing_log.error_message = None
            existing_log.email_status = despacho.email_status
            existing_log.email_error = despacho.email_error
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "falha ao gravar log de sucesso do alerta contrato=%s janela=%s",
                contrato.id,
                janela_str,
            )
            await db.rollback()
            failed += 1
            results.append(
                AlertaResult(
                    contrato_id=contrato.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message="db_commit_failed",
                )
            )
            continue
        sent += 1
        results.append(
            AlertaResult(
                contrato_id=contrato.id,
                janela=janela_str,
                status="sent",
                recipients=list(recipients),
                email_status=despacho.email_status,
                email_error=despacho.email_error,
            )
        )

    return AlertaSummary(
        total_contratos=len(contratos),
        sent=sent,
        skipped=skipped,
        failed=failed,
        results=results,
    )


__all__ = [
    "AlertaResult",
    "AlertaSummary",
    "dispatch_contrato_alerts",
    "render_alerta_contrato_html",
]
