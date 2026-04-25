"""Gestao de CNDs / atestados da empresa (D.6).

Domınio operacional:

- A Primor mantem dezenas de documentos com vencimento (CND federal,
  FGTS, CNDT, certidoes estaduais/municipais, atestados CAT). Esses
  documentos sao exigidos na fase de habilitacao das licitacoes -- se
  expirar, o pregao e perdido.
- Este modulo registra cada documento (`CertidaoEmpresa`), expoe CRUD,
  calcula o status atual (vigente / vencendo / vencido) e dispara
  alertas por email (Resend) em janelas pre-definidas (30/15/7/0 dias).

Os alertas sao idempotentes: a tabela `CertidaoAlertaLog` tem unique
constraint `(certidao_id, janela)` -- o cron diario do worker nunca
manda o mesmo email duas vezes.

Atestados CAT *podem* nao ter validade (sao perenes); nesse caso
`validade=None` e o cron os ignora.
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
from app.integrations.resend.client import ResendClient, ResendError
from app.modules.licitacoes.models import CertidaoAlertaLog, CertidaoEmpresa

logger = logging.getLogger(__name__)


# Tipos canonicos. O banco aceita string livre para permitir tipos custom
# sem migracao, mas a UI / autocompletes devem usar essa lista.
TIPOS_CERTIDAO: tuple[tuple[str, str], ...] = (
    ("CND_FEDERAL", "Certidao Negativa de Debitos Federais"),
    ("FGTS", "Certificado de Regularidade do FGTS (CRF)"),
    ("CNDT", "Certidao Negativa de Debitos Trabalhistas"),
    ("INSS", "Certidao Negativa de Debitos Previdenciarios"),
    ("ESTADUAL", "Certidao Negativa Estadual"),
    ("MUNICIPAL", "Certidao Negativa Municipal"),
    ("FALENCIA", "Certidao de Falencia / Recuperacao Judicial"),
    ("CADIN", "Certidao Negativa CADIN"),
    ("ATESTADO_CAT", "Atestado de Capacidade Tecnica (CAT)"),
    ("ACERVO_TECNICO", "Certidao de Acervo Tecnico (CAT)"),
    ("OUTRO", "Outro"),
)
TIPOS_VALIDOS: frozenset[str] = frozenset(t for t, _ in TIPOS_CERTIDAO)

# Janelas de alerta (em dias antes do vencimento).
# 0 = vencimento; valores negativos seriam para alertas pos-vencimento
# (pode entrar na proxima iteracao). 30/15/7/0 cobrem o ciclo padrao
# das licitacoes (publicacao -> sessao tipicamente em 8-30 dias).
JANELAS_ALERTA: tuple[int, ...] = (30, 15, 7, 0)

VENCENDO_DIAS_LIMITE = 30  # status "vencendo" se faltarem <= 30 dias


# ----------------------------- domain helpers ---------------------------------


def compute_status(validade: _date | None, *, today: _date | None = None) -> str:
    """Retorna status textual para uso na UI / filtros.

    - `sem_validade` quando `validade is None` (atestados perpetuos).
    - `vencido` quando `today > validade`.
    - `vencendo` quando faltam <= `VENCENDO_DIAS_LIMITE` dias.
    - `vigente` caso contrario.
    """
    if validade is None:
        return "sem_validade"
    today = today or _date.today()
    delta = (validade - today).days
    if delta < 0:
        return "vencido"
    if delta <= VENCENDO_DIAS_LIMITE:
        return "vencendo"
    return "vigente"


def janela_for_certidao(
    validade: _date | None, *, today: _date | None = None
) -> int | None:
    """Maior janela de alerta aplicavel hoje, ou None.

    Se a validade ja passou ha 1+ dia, devolve None -- assumimos que o
    alerta de vencimento (`0d`) ja foi enviado. Para alertas pos-vencimento
    recorrentes (ex.: lembrete mensal apos vencer), usar uma janela
    separada (fora do escopo do D.6 inicial).
    """
    if validade is None:
        return None
    today = today or _date.today()
    delta = (validade - today).days
    if delta < 0:
        return None
    # Pega a maior janela <= delta (a mais "urgente" ainda nao acionada).
    # Ex.: faltam 12 dias -> janela 7 (a 15 ja deveria ter sido enviada).
    candidatas = [j for j in JANELAS_ALERTA if j >= delta]
    if not candidatas:
        return None
    return min(candidatas)


# ------------------------------- service --------------------------------------


@dataclass(slots=True)
class AlertaResult:
    certidao_id: int
    janela: str
    status: str  # "sent" | "skipped_already_sent" | "skipped_no_validade" | "failed"
    recipients: list[str]
    resend_message_id: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class AlertaSummary:
    total_certidoes: int
    sent: int
    skipped: int
    failed: int
    results: list[AlertaResult]


async def list_certidoes(
    db: AsyncSession,
    *,
    empresa_cnpj: str | None = None,
    tipo: str | None = None,
    status: str | None = None,
    today: _date | None = None,
) -> list[CertidaoEmpresa]:
    stmt = select(CertidaoEmpresa).order_by(CertidaoEmpresa.validade.asc().nullslast())
    if empresa_cnpj:
        stmt = stmt.where(CertidaoEmpresa.empresa_cnpj == empresa_cnpj)
    if tipo:
        stmt = stmt.where(CertidaoEmpresa.tipo == tipo)
    rows = list((await db.execute(stmt)).scalars().all())
    if status:
        today = today or _date.today()
        rows = [r for r in rows if compute_status(r.validade, today=today) == status]
    return rows


async def get_certidao(
    db: AsyncSession, certidao_id: int
) -> CertidaoEmpresa | None:
    return await db.get(CertidaoEmpresa, certidao_id)


async def create_certidao(
    db: AsyncSession,
    *,
    empresa_cnpj: str,
    tipo: str,
    numero: str | None = None,
    emissao: _date | None = None,
    validade: _date | None = None,
    arquivo_path: str | None = None,
    orgao_emissor: str | None = None,
    observacoes: str | None = None,
) -> CertidaoEmpresa:
    if tipo not in TIPOS_VALIDOS:
        # Aceitamos string livre para "OUTRO/custom", mas avisamos.
        logger.info("certidao tipo nao canonico: %s", tipo)
    row = CertidaoEmpresa(
        empresa_cnpj=empresa_cnpj,
        tipo=tipo,
        numero=numero,
        emissao=emissao,
        validade=validade,
        arquivo_path=arquivo_path,
        orgao_emissor=orgao_emissor,
        observacoes=observacoes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_certidao(
    db: AsyncSession,
    certidao_id: int,
    **fields: object,
) -> CertidaoEmpresa | None:
    row = await db.get(CertidaoEmpresa, certidao_id)
    if row is None:
        return None
    for key, value in fields.items():
        if hasattr(row, key) and value is not None:
            setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_certidao(db: AsyncSession, certidao_id: int) -> bool:
    row = await db.get(CertidaoEmpresa, certidao_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


# --------------------------- alertas (cron diario) ----------------------------


async def _alerta_already_sent(
    db: AsyncSession, certidao_id: int, janela: str
) -> bool:
    stmt = (
        select(CertidaoAlertaLog.id)
        .where(CertidaoAlertaLog.certidao_id == certidao_id)
        .where(CertidaoAlertaLog.janela == janela)
        .where(CertidaoAlertaLog.status == "sent")
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


def render_alerta_html(
    certidao: CertidaoEmpresa,
    *,
    janela: int,
    public_base_url: str,
) -> str:
    tipo_label = dict(TIPOS_CERTIDAO).get(certidao.tipo, certidao.tipo)
    if janela == 0:
        urgencia = "VENCIDA hoje"
        cor = "#dc2626"
    elif janela <= 7:
        urgencia = f"Vence em {janela} dia(s)"
        cor = "#ea580c"
    elif janela <= 15:
        urgencia = f"Vence em {janela} dias"
        cor = "#d97706"
    else:
        urgencia = f"Vence em {janela} dias"
        cor = "#0284c7"
    validade_str = (
        certidao.validade.strftime("%d/%m/%Y") if certidao.validade else "-"
    )
    dashboard_url = f"{public_base_url.rstrip('/')}/licitacoes/certidoes"
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 640px; margin: 0 auto; color: #0f172a;">
      <h2 style="margin: 0 0 4px;">Certidao com vencimento proximo</h2>
      <p style="margin: 0 0 16px; color: {cor}; font-weight: 600; font-size: 14px;">
        {escape(urgencia)}
      </p>
      <table style="width: 100%; border-collapse: collapse; border: 1px solid #e5e7eb;">
        <tbody>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc; width: 30%;">Tipo</td>
            <td style="padding: 8px; font-size: 13px;">{escape(tipo_label)}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">CNPJ</td>
            <td style="padding: 8px; font-size: 13px;">{escape(certidao.empresa_cnpj)}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Numero</td>
            <td style="padding: 8px; font-size: 13px;">{escape(certidao.numero or "-")}</td>
          </tr>
          <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Validade</td>
            <td style="padding: 8px; font-size: 13px; color: {cor}; font-weight: 600;">{escape(validade_str)}</td>
          </tr>
          <tr>
            <td style="padding: 8px; font-size: 13px; background: #f8fafc;">Orgao Emissor</td>
            <td style="padding: 8px; font-size: 13px;">{escape(certidao.orgao_emissor or "-")}</td>
          </tr>
        </tbody>
      </table>
      <p style="margin-top: 20px; font-size: 13px;">
        <a href="{escape(dashboard_url)}" style="color: #2563eb; text-decoration: none;">
          Abrir Motor Central -> Certidoes ->
        </a>
      </p>
      <p style="margin-top: 24px; font-size: 11px; color: #94a3b8;">
        Voce esta recebendo este alerta porque foi cadastrado como destinatario
        de notificacoes de vencimento. Janelas: 30, 15, 7 e 0 dias antes do vencimento.
      </p>
    </div>
    """.strip()


async def dispatch_expiration_alerts(
    db: AsyncSession,
    resend: ResendClient,
    *,
    recipients: Sequence[str],
    public_base_url: str | None = None,
    from_email: str | None = None,
    today: _date | None = None,
) -> AlertaSummary:
    """Itera todas as certidoes e dispara alertas de vencimento.

    Para cada certidao:
    - Calcula `janela` ativa (None se ja vencida, sem validade, ou ainda
      muito longe da janela mais larga (30d)).
    - Verifica se ja foi enviado para essa (certidao_id, janela). Se sim,
      pula (idempotente).
    - Se houver janela ativa e nao houver log, envia o email e registra.

    Falhas isoladas: 1 envio que falha nao aborta os demais.
    """
    settings = get_settings()
    public_base_url = public_base_url or settings.public_base_url
    from_email = from_email or settings.resend_from_email
    today = today or _date.today()

    if not recipients:
        # Sem destinatarios -> sem trabalho. Util em CI / dev.
        logger.warning("dispatch_expiration_alerts: lista de recipients vazia")
        return AlertaSummary(0, 0, 0, 0, [])

    stmt = select(CertidaoEmpresa)
    certidoes = list((await db.execute(stmt)).scalars().all())

    sent = 0
    skipped = 0
    failed = 0
    results: list[AlertaResult] = []

    for certidao in certidoes:
        janela = janela_for_certidao(certidao.validade, today=today)
        if janela is None:
            # Sem validade ou ja vencida (alertas pos-vencimento ficam fora deste loop).
            skipped += 1
            results.append(
                AlertaResult(
                    certidao_id=certidao.id,
                    janela="none",
                    status="skipped_no_validade",
                    recipients=list(recipients),
                )
            )
            continue

        janela_str = f"{janela}d"
        if await _alerta_already_sent(db, certidao.id, janela_str):
            skipped += 1
            results.append(
                AlertaResult(
                    certidao_id=certidao.id,
                    janela=janela_str,
                    status="skipped_already_sent",
                    recipients=list(recipients),
                )
            )
            continue

        html = render_alerta_html(
            certidao, janela=janela, public_base_url=public_base_url
        )
        tipo_label = dict(TIPOS_CERTIDAO).get(certidao.tipo, certidao.tipo)
        validade_str = (
            certidao.validade.strftime("%d/%m/%Y") if certidao.validade else "-"
        )
        if janela == 0:
            subject = f"[Motor Central] VENCIDA: {tipo_label} ({validade_str})"
        else:
            subject = (
                f"[Motor Central] Certidao vence em {janela} dias: "
                f"{tipo_label} ({validade_str})"
            )

        try:
            resp = await resend.send_email(
                to=list(recipients),
                subject=subject,
                html=html,
                from_=from_email,
            )
        except (ResendError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "alerta certidao=%s janela=%s falhou: %s",
                certidao.id,
                janela_str,
                exc,
                exc_info=False,
            )
            log = CertidaoAlertaLog(
                certidao_id=certidao.id,
                janela=janela_str,
                recipients=list(recipients),
                status="failed",
                error_message=str(exc)[:1024],
            )
            db.add(log)
            failed += 1
            results.append(
                AlertaResult(
                    certidao_id=certidao.id,
                    janela=janela_str,
                    status="failed",
                    recipients=list(recipients),
                    error_message=str(exc)[:1024],
                )
            )
            continue

        message_id = resp.get("id") if isinstance(resp, dict) else None
        log = CertidaoAlertaLog(
            certidao_id=certidao.id,
            janela=janela_str,
            recipients=list(recipients),
            resend_message_id=message_id,
            status="sent",
        )
        db.add(log)
        sent += 1
        results.append(
            AlertaResult(
                certidao_id=certidao.id,
                janela=janela_str,
                status="sent",
                recipients=list(recipients),
                resend_message_id=message_id,
            )
        )

    await db.commit()

    return AlertaSummary(
        total_certidoes=len(certidoes),
        sent=sent,
        skipped=skipped,
        failed=failed,
        results=results,
    )


# Re-export for tests.
__all__ = [
    "AlertaResult",
    "AlertaSummary",
    "JANELAS_ALERTA",
    "TIPOS_CERTIDAO",
    "TIPOS_VALIDOS",
    "VENCENDO_DIAS_LIMITE",
    "compute_status",
    "create_certidao",
    "delete_certidao",
    "dispatch_expiration_alerts",
    "get_certidao",
    "janela_for_certidao",
    "list_certidoes",
    "render_alerta_html",
    "update_certidao",
]
