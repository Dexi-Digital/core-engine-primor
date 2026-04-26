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

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
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

# Mutacoes em CNDs/atestados sao sensiveis -- documentos de habilitacao
# em licitacao publica. Toda criacao/edicao/exclusao precisa virar uma
# linha em audit_log (AGENTS.md). Default `system` cobre paths sem usuario
# logado (worker de import futuro etc.); requests HTTP devem passar
# `actor=current_user.email` -- ver certidoes_router.
_AUDIT_RESOURCE = "licitacoes.certidao"
_AUDIT_ACTOR_PLACEHOLDER = "system"


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
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )
    await db.commit()


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
    # Pega a menor janela >= delta (a mais "urgente" ainda nao acionada).
    # Ex.: faltam 12 dias -> janela 15 (a 7 ainda nao atingiu o threshold).
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


@dataclass(slots=True)
class _CertidaoSnapshot:
    """Snapshot dos campos da certidao usados no dispatch.

    Mesmo motivo do `_EmployeeSnapshot` em `dp_sesmt/aso_alerts.py`:
    apos `db.rollback()` (per-certidao error path) o SQLAlchemy 2.0
    expira TODOS os ORM objects da sessao, e o lazy-refresh em
    `AsyncSession` falha com `MissingGreenlet`. Carregar os campos
    uma vez em dataclass puro isola o loop dessa armadilha.
    """

    id: int
    tipo: str
    numero: str | None
    empresa_cnpj: str
    orgao_emissor: str | None
    validade: _date | None  # `None` = certidao sem validade (atestado, etc.)


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
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
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
    await _record_audit(
        db,
        action="create",
        resource_id=row.id,
        actor=actor,
        metadata={
            "empresa_cnpj": row.empresa_cnpj,
            "tipo": row.tipo,
            "validade": row.validade,
        },
    )
    return row


async def update_certidao(
    db: AsyncSession,
    certidao_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
    **fields: object,
) -> CertidaoEmpresa | None:
    row = await db.get(CertidaoEmpresa, certidao_id)
    if row is None:
        return None
    # Permite limpar campos nullable explicitamente (ex: PUT validade=null para
    # converter uma certidao em "sem validade"). O router ja usa
    # exclude_unset=True, entao so chegam aqui campos que o cliente enviou.
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


async def delete_certidao(
    db: AsyncSession,
    certidao_id: int,
    *,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> bool:
    row = await db.get(CertidaoEmpresa, certidao_id)
    if row is None:
        return False
    snapshot = {
        "empresa_cnpj": row.empresa_cnpj,
        "tipo": row.tipo,
        "validade": row.validade,
    }
    await db.delete(row)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource_id=certidao_id,
        actor=actor,
        metadata=snapshot,
    )
    return True


# --------------------------- alertas (cron diario) ----------------------------


async def _alerta_already_sent(
    db: AsyncSession, certidao_id: int, janela: str
) -> bool:
    """Verifica se ja existe envio bem-sucedido para essa janela.

    Note que `failed` nao conta -- entradas falhas devem ser tentadas
    novamente no proximo cron, atualizando o log existente in-place
    (a UniqueConstraint `(certidao_id, janela)` impede duplicatas).
    """
    stmt = (
        select(CertidaoAlertaLog.id)
        .where(CertidaoAlertaLog.certidao_id == certidao_id)
        .where(CertidaoAlertaLog.janela == janela)
        .where(CertidaoAlertaLog.status == "sent")
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _get_existing_log(
    db: AsyncSession, certidao_id: int, janela: str
) -> CertidaoAlertaLog | None:
    stmt = (
        select(CertidaoAlertaLog)
        .where(CertidaoAlertaLog.certidao_id == certidao_id)
        .where(CertidaoAlertaLog.janela == janela)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def render_alerta_html(
    certidao: CertidaoEmpresa | _CertidaoSnapshot,
    *,
    janela: int,
    public_base_url: str,
    dias_restantes: int | None = None,
) -> str:
    """Render do email de alerta.

    `janela` define a *cor / urgencia* do alerta (30/15/7/0); `dias_restantes`
    e o numero real de dias ate o vencimento, exibido para o usuario. Em
    licitacoes essa diferenca importa: uma certidao com 12 dias restantes
    cai na janela `15`, mas o email precisa dizer "12 dias", nao "15 dias".

    Quando `dias_restantes` nao e fornecido (chamada legada / fallback),
    cai para o valor da `janela` -- mantem retrocompat mas perde precisao.
    """
    tipo_label = dict(TIPOS_CERTIDAO).get(certidao.tipo, certidao.tipo)
    dias = dias_restantes if dias_restantes is not None else janela
    if janela == 0:
        urgencia = "VENCIDA hoje" if dias == 0 else f"Vence em {dias} dia(s)"
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

    stmt = select(
        CertidaoEmpresa.id,
        CertidaoEmpresa.tipo,
        CertidaoEmpresa.numero,
        CertidaoEmpresa.empresa_cnpj,
        CertidaoEmpresa.orgao_emissor,
        CertidaoEmpresa.validade,
    )
    rows = (await db.execute(stmt)).all()
    # Snapshot detached -- ver doc da `_CertidaoSnapshot`. NAO filtramos
    # `validade IS NOT NULL` aqui porque queremos preservar o comportamento
    # antigo de contabilizar `skipped+1` para atestados sem validade.
    certidoes: list[_CertidaoSnapshot] = [
        _CertidaoSnapshot(
            id=r.id,
            tipo=r.tipo,
            numero=r.numero,
            empresa_cnpj=r.empresa_cnpj,
            orgao_emissor=r.orgao_emissor,
            validade=r.validade,
        )
        for r in rows
    ]

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

        # Calcula os dias reais ate o vencimento -- diferente da janela
        # (que e o threshold do alerta, nao a contagem real). Usado tanto
        # no subject quanto no corpo do email.
        assert certidao.validade is not None  # janela_for_certidao filtrou None
        dias_restantes = (certidao.validade - today).days

        html = render_alerta_html(
            certidao,
            janela=janela,
            public_base_url=public_base_url,
            dias_restantes=dias_restantes,
        )
        tipo_label = dict(TIPOS_CERTIDAO).get(certidao.tipo, certidao.tipo)
        validade_str = certidao.validade.strftime("%d/%m/%Y")
        if dias_restantes == 0:
            subject = f"[Motor Central] VENCIDA hoje: {tipo_label} ({validade_str})"
        else:
            subject = (
                f"[Motor Central] Certidao vence em {dias_restantes} dia(s): "
                f"{tipo_label} ({validade_str})"
            )

        # Carrega log existente uma unica vez antes de enviar -- usado tanto
        # no branch de erro (UPDATE para nao violar UniqueConstraint) quanto
        # no branch de sucesso (UPDATE failed -> sent).
        existing_log = await _get_existing_log(db, certidao.id, janela_str)

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
            error_msg = str(exc)[:1024]
            if existing_log is None:
                db.add(
                    CertidaoAlertaLog(
                        certidao_id=certidao.id,
                        janela=janela_str,
                        recipients=list(recipients),
                        status="failed",
                        error_message=error_msg,
                    )
                )
            else:
                # Retry de uma falha anterior que tornou a falhar -- atualiza
                # in-place para nao violar UniqueConstraint(certidao_id, janela).
                existing_log.recipients = list(recipients)
                existing_log.status = "failed"
                existing_log.error_message = error_msg
            # Commit per-certidao para que 1 falha em uma certidao nao
            # rollback as ja enviadas com sucesso nesta rodada.
            try:
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "falha ao gravar log de erro do alerta certidao=%s janela=%s",
                    certidao.id,
                    janela_str,
                )
                await db.rollback()
            failed += 1
            results.append(
                AlertaResult(
                    certidao_id=certidao.id,
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
                CertidaoAlertaLog(
                    certidao_id=certidao.id,
                    janela=janela_str,
                    recipients=list(recipients),
                    resend_message_id=message_id,
                    status="sent",
                )
            )
        else:
            # Retry bem-sucedido apos falha previa -- promove o log para sent.
            existing_log.recipients = list(recipients)
            existing_log.resend_message_id = message_id
            existing_log.status = "sent"
            existing_log.error_message = None
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "falha ao gravar log de sucesso do alerta certidao=%s janela=%s",
                certidao.id,
                janela_str,
            )
            await db.rollback()
            failed += 1
            results.append(
                AlertaResult(
                    certidao_id=certidao.id,
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
                certidao_id=certidao.id,
                janela=janela_str,
                status="sent",
                recipients=list(recipients),
                resend_message_id=message_id,
            )
        )

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
