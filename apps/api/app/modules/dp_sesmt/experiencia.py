"""Alerta de fim de contrato de experiencia (demanda #2 do briefing).

Regra CLT (art. 445, paragrafo unico + art. 451): contrato de
experiencia dura no maximo 90 dias e admite UMA prorrogacao. Na
pratica o RH divide em dois periodos; dois marcos pedem decisao:

- **prorrogacao** -- fim do 1o periodo: prorrogar ou desligar.
- **efetivacao**  -- fim da experiencia: efetivar (vira prazo
  indeterminado automaticamente se continuar trabalhando) ou desligar.

**Premissa a confirmar com o cliente:** o cadastro NAO guarda a divisao
dos periodos. Quando `tipo_contrato` traz o padrao "A+B" (ex.:
"Experiencia 30+60") usamos esses numeros; caso contrario assumimos
**45+45** (`PERIODOS_PADRAO`) e marcamos `periodos_assumidos=True` na
resposta para a tela deixar isso visivel.

Contagem: o dia da admissao e o 1o dia do contrato, entao o fim do
periodo de N dias e `admissao + (N - 1)`. Ex.: admissao 01/03 -> 45o dia
em 14/04 e 90o dia em 29/05.

**Quem e considerado em experiencia** (`tipo_contrato` e texto livre --
o formulario sugere "CLT / PJ / Estagio" e o seed grava "CLT"):

1. `tipo_contrato` menciona experiencia ("Experiencia", "CLT -
   Experiência", "exp 45+45"...), comparado sem acento/caixa; OU
2. o funcionario tem `EmployeeDocument` do tipo `CONTRATO_EXPERIENCIA`
   e o `tipo_contrato` nao e incompativel (PJ, estagio, aprendiz,
   temporario, autonomo).

Em ambos os casos: `status == ativo`, com `data_admissao` preenchida e
experiencia ainda nao encerrada (admissao ha menos de 90 dias).
Quem foi efetivado antes do prazo sai da lista trocando o
`tipo_contrato` (ex.: para "CLT").

Entrega: notificacao IN-APP (`app.modules.notificacoes`), uma por
destinatario. Destinatarios da env `EXPERIENCIA_ALERT_EMAILS` (CSV),
com fallback para `ASO_ALERT_EMAILS` (mesmo publico: RH/DP).

Idempotencia: a `chave_idempotencia` da notificacao inclui funcionario,
marco, data do marco e janela -- rodar o cron 2x no dia nao duplica, e
corrigir a data de admissao gera alerta novo para a data certa.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt.afastamentos import janela_for
from app.modules.dp_sesmt.models import (
    DOC_EMP_CONTRATO_EXPERIENCIA,
    STATUS_ATIVO,
    Employee,
    EmployeeDocument,
)
from app.modules.notificacoes.models import CATEGORIA_DP, Notificacao
from app.modules.notificacoes.service import criar_notificacao

logger = structlog.get_logger(__name__)

DURACAO_MAXIMA_DIAS = 90
# PREMISSA (confirmar com a Primor): divisao padrao quando o cadastro
# nao informa os periodos.
PERIODOS_PADRAO: tuple[int, int] = (45, 45)

# Janelas de alerta (dias antes do marco). Mais curtas que as do ASO
# (30/15/7/0) porque o 1o periodo inteiro tem 45 dias -- avisar com 30
# seria avisar na 2a semana de trabalho. Mesmas da pericia INSS.
JANELAS_ALERTA: tuple[int, ...] = (15, 7, 0)

MARCO_PRORROGACAO = "prorrogacao"
MARCO_EFETIVACAO = "efetivacao"

ORIGEM_TIPO_CONTRATO = "tipo_contrato"
ORIGEM_DOCUMENTO = "documento"

ENV_RECIPIENTS = "EXPERIENCIA_ALERT_EMAILS"
ENV_RECIPIENTS_FALLBACK = "ASO_ALERT_EMAILS"

_RE_PERIODOS = re.compile(r"(\d{1,2})\s*(?:\+|x|/|e)\s*(\d{1,2})")
_RE_EXP_TOKEN = re.compile(r"\bexp\b")
_INCOMPATIVEIS_PREFIXOS = ("estag", "aprendiz", "temporari", "autonom", "terceir")


# --- regras puras -----------------------------------------------------------


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def is_contrato_experiencia(tipo_contrato: str | None) -> bool:
    """`tipo_contrato` declara experiencia explicitamente?"""
    n = _normalizar(tipo_contrato)
    return "experienc" in n or bool(_RE_EXP_TOKEN.search(n))


def is_tipo_incompativel(tipo_contrato: str | None) -> bool:
    """Vinculos que nao sao CLT por prazo de experiencia."""
    n = _normalizar(tipo_contrato)
    if not n:
        return False
    tokens = [t for t in re.split(r"[^a-z]+", n) if t]
    return "pj" in tokens or any(
        t.startswith(p) for t in tokens for p in _INCOMPATIVEIS_PREFIXOS
    )


def parse_periodos(tipo_contrato: str | None) -> tuple[tuple[int, int], bool]:
    """Extrai "A+B" do `tipo_contrato`. Devolve (periodos, assumido).

    So aceita divisoes validas pela CLT (A > 0, B >= 0, A + B <= 90);
    qualquer outra coisa cai na premissa `PERIODOS_PADRAO`.
    """
    m = _RE_PERIODOS.search(_normalizar(tipo_contrato))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > 0 and b >= 0 and a + b <= DURACAO_MAXIMA_DIAS:
            return (a, b), False
    return PERIODOS_PADRAO, True


def fim_do_periodo(admissao: _date, dias: int) -> _date:
    """Ultimo dia de um periodo de `dias` contado a partir da admissao."""
    return admissao + timedelta(days=dias - 1)


@dataclass(slots=True)
class PrazoExperiencia:
    employee_id: int
    nome_completo: str
    cargo: str | None
    obra: str | None
    tipo_contrato: str | None
    data_admissao: _date
    primeiro_periodo_dias: int
    segundo_periodo_dias: int
    periodos_assumidos: bool
    fim_primeiro_periodo: _date
    fim_experiencia: _date
    origem: str
    proximo_marco: str | None
    data_proximo_marco: _date | None
    dias_restantes: int | None


def compute_prazo(
    *,
    employee_id: int,
    nome_completo: str,
    cargo: str | None,
    obra: str | None,
    tipo_contrato: str | None,
    data_admissao: _date,
    origem: str,
    today: _date,
) -> PrazoExperiencia:
    (p1, p2), assumido = parse_periodos(tipo_contrato)
    fim1 = fim_do_periodo(data_admissao, p1)
    fim = fim_do_periodo(data_admissao, p1 + p2)

    marco: str | None
    data_marco: _date | None
    if p2 > 0 and today <= fim1:
        marco, data_marco = MARCO_PRORROGACAO, fim1
    elif today <= fim:
        marco, data_marco = MARCO_EFETIVACAO, fim
    else:
        marco, data_marco = None, None

    return PrazoExperiencia(
        employee_id=employee_id,
        nome_completo=nome_completo,
        cargo=cargo,
        obra=obra,
        tipo_contrato=tipo_contrato,
        data_admissao=data_admissao,
        primeiro_periodo_dias=p1,
        segundo_periodo_dias=p2,
        periodos_assumidos=assumido,
        fim_primeiro_periodo=fim1,
        fim_experiencia=fim,
        origem=origem,
        proximo_marco=marco,
        data_proximo_marco=data_marco,
        dias_restantes=(data_marco - today).days if data_marco else None,
    )


def recipients_from_env() -> list[str]:
    raw = os.getenv(ENV_RECIPIENTS, "").strip() or os.getenv(
        ENV_RECIPIENTS_FALLBACK, ""
    ).strip()
    return [e.strip() for e in raw.split(",") if e.strip()]


# --- consulta ---------------------------------------------------------------


async def list_prazos_experiencia(
    db: AsyncSession,
    *,
    today: _date | None = None,
    horizonte_dias: int | None = None,
) -> list[PrazoExperiencia]:
    """Funcionarios em experiencia com marco ainda por vir.

    Ordenado pelo marco mais proximo. `horizonte_dias` limita a quem tem
    marco em ate N dias (None = todos em experiencia).
    """
    today = today or _date.today()
    # Admissao ha menos de 90 dias: fim_experiencia >= today.
    limite = today - timedelta(days=DURACAO_MAXIMA_DIAS - 1)

    rows = (
        await db.execute(
            select(
                Employee.id,
                Employee.nome_completo,
                Employee.cargo,
                Employee.obra,
                Employee.tipo_contrato,
                Employee.data_admissao,
            )
            .where(Employee.status == STATUS_ATIVO)
            .where(Employee.data_admissao.is_not(None))
            .where(Employee.data_admissao >= limite)
        )
    ).all()
    if not rows:
        return []

    com_documento = set(
        (
            await db.execute(
                select(EmployeeDocument.employee_id)
                .where(EmployeeDocument.tipo == DOC_EMP_CONTRATO_EXPERIENCIA)
                .where(EmployeeDocument.employee_id.in_([r.id for r in rows]))
                .distinct()
            )
        ).scalars()
    )

    prazos: list[PrazoExperiencia] = []
    for r in rows:
        if is_contrato_experiencia(r.tipo_contrato):
            origem = ORIGEM_TIPO_CONTRATO
        elif r.id in com_documento and not is_tipo_incompativel(r.tipo_contrato):
            origem = ORIGEM_DOCUMENTO
        else:
            continue
        prazo = compute_prazo(
            employee_id=r.id,
            nome_completo=r.nome_completo,
            cargo=r.cargo,
            obra=r.obra,
            tipo_contrato=r.tipo_contrato,
            data_admissao=r.data_admissao,
            origem=origem,
            today=today,
        )
        if prazo.data_proximo_marco is None:
            continue
        if horizonte_dias is not None and (
            prazo.dias_restantes is None or prazo.dias_restantes > horizonte_dias
        ):
            continue
        prazos.append(prazo)

    prazos.sort(key=lambda p: (p.data_proximo_marco, p.nome_completo))
    return prazos


# --- dispatch ---------------------------------------------------------------


@dataclass(slots=True)
class ExperienciaAlertaResult:
    employee_id: int
    marco: str
    janela: str
    status: str
    recipients: list[str]
    error_message: str | None = None


@dataclass(slots=True)
class ExperienciaAlertaSummary:
    total_employees: int
    sent: int
    skipped: int
    failed: int
    results: list[ExperienciaAlertaResult]


def chave_alerta(prazo: PrazoExperiencia, janela: int) -> str:
    assert prazo.proximo_marco and prazo.data_proximo_marco
    return (
        f"experiencia:{prazo.employee_id}:{prazo.proximo_marco}:"
        f"{prazo.data_proximo_marco.isoformat()}:{janela}d"
    )


def render_alerta(prazo: PrazoExperiencia) -> tuple[str, str]:
    """(titulo, corpo) da notificacao."""
    assert prazo.data_proximo_marco is not None
    dias = prazo.dias_restantes or 0
    quando = "HOJE" if dias == 0 else f"em {dias} dia(s)"
    data_str = prazo.data_proximo_marco.strftime("%d/%m/%Y")
    if prazo.proximo_marco == MARCO_PRORROGACAO:
        titulo = (
            f"Experiência: fim do 1º período {quando} — {prazo.nome_completo}"
        )
        acao = "Decidir se prorroga o contrato de experiência ou desliga."
        marco_txt = (
            f"Fim do 1º período ({prazo.primeiro_periodo_dias} dias): {data_str}"
        )
    else:
        titulo = f"Experiência: termina {quando} — {prazo.nome_completo}"
        acao = (
            "Decidir se efetiva ou desliga. Se continuar trabalhando após "
            "essa data, o contrato vira prazo indeterminado automaticamente."
        )
        marco_txt = (
            f"Fim da experiência "
            f"({prazo.primeiro_periodo_dias + prazo.segundo_periodo_dias} dias): "
            f"{data_str}"
        )
    linhas = [
        acao,
        marco_txt,
        f"Admissão: {prazo.data_admissao.strftime('%d/%m/%Y')}",
        f"Cargo: {prazo.cargo or '-'} · Obra: {prazo.obra or '-'}",
    ]
    if prazo.periodos_assumidos:
        linhas.append(
            "Divisão 45+45 assumida (cadastro não informa os períodos)."
        )
    return titulo, "\n".join(linhas)


async def _ja_enviado(
    db: AsyncSession, chave: str, recipients: Sequence[str]
) -> bool:
    destinatarios = {r.strip().lower() for r in recipients}
    existentes = set(
        (
            await db.execute(
                select(Notificacao.destinatario)
                .where(Notificacao.chave_idempotencia == chave)
                .where(Notificacao.destinatario.in_(destinatarios))
            )
        ).scalars()
    )
    return destinatarios <= existentes


async def dispatch_experiencia_alerts(
    db: AsyncSession,
    *,
    recipients: Sequence[str],
    today: _date | None = None,
) -> ExperienciaAlertaSummary:
    """Gera notificacoes in-app para marcos de experiencia em janela.

    Falhas isoladas: erro em um funcionario nao aborta os demais.
    """
    today = today or _date.today()
    if not recipients:
        logger.warning("dispatch_experiencia_alerts: lista de recipients vazia")
        return ExperienciaAlertaSummary(0, 0, 0, 0, [])

    # `list_prazos_experiencia` devolve dataclasses (snapshot), entao um
    # rollback no meio do loop nao expira nada que usamos depois.
    prazos = await list_prazos_experiencia(db, today=today)

    sent = skipped = failed = 0
    results: list[ExperienciaAlertaResult] = []
    for prazo in prazos:
        marco = prazo.proximo_marco or "none"
        janela = janela_for(prazo.data_proximo_marco, JANELAS_ALERTA, today=today)
        if janela is None:
            skipped += 1
            results.append(
                ExperienciaAlertaResult(
                    prazo.employee_id, marco, "none", "skipped_no_window",
                    list(recipients),
                )
            )
            continue

        janela_str = f"{janela}d"
        chave = chave_alerta(prazo, janela)
        if await _ja_enviado(db, chave, recipients):
            skipped += 1
            results.append(
                ExperienciaAlertaResult(
                    prazo.employee_id, marco, janela_str,
                    "skipped_already_sent", list(recipients),
                )
            )
            continue

        titulo, corpo = render_alerta(prazo)
        try:
            for destinatario in recipients:
                await criar_notificacao(
                    db,
                    destinatario=destinatario,
                    titulo=titulo[:255],
                    corpo=corpo,
                    categoria=CATEGORIA_DP,
                    link=f"/rh/funcionarios/{prazo.employee_id}",
                    chave_idempotencia=chave,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "alerta experiencia falhou",
                employee_id=prazo.employee_id,
                marco=marco,
                janela=janela_str,
                error=str(exc),
            )
            await db.rollback()
            failed += 1
            results.append(
                ExperienciaAlertaResult(
                    prazo.employee_id, marco, janela_str, "failed",
                    list(recipients), error_message=str(exc)[:1024],
                )
            )
            continue

        sent += 1
        results.append(
            ExperienciaAlertaResult(
                prazo.employee_id, marco, janela_str, "sent", list(recipients)
            )
        )

    return ExperienciaAlertaSummary(
        total_employees=len(prazos),
        sent=sent,
        skipped=skipped,
        failed=failed,
        results=results,
    )


__all__ = [
    "DURACAO_MAXIMA_DIAS",
    "ENV_RECIPIENTS",
    "JANELAS_ALERTA",
    "MARCO_EFETIVACAO",
    "MARCO_PRORROGACAO",
    "PERIODOS_PADRAO",
    "ExperienciaAlertaSummary",
    "PrazoExperiencia",
    "compute_prazo",
    "dispatch_experiencia_alerts",
    "is_contrato_experiencia",
    "is_tipo_incompativel",
    "list_prazos_experiencia",
    "parse_periodos",
    "recipients_from_env",
]
