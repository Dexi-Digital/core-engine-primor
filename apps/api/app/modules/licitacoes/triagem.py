"""Workflow de triagem do Captador de Licitacoes (Squad 1).

Maquina de status vinda do Projeto Tecnico do cliente (23/06/2026):
a analista aprova/rejeita/observa editais captados; a aprovacao dispara
(o processamento da Squad 2: pasta + anexos + planilha orcamentaria).

Este arquivo segue o formato de `certidoes.py`: constantes de dominio +
funcoes de servico async no mesmo modulo.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.config import get_settings
from app.modules.licitacoes.models import (
    DecisaoTriagem,
    Edital,
    Licitacao,
    PastaProjeto,
    PlanilhaOrcamentaria,
)
from app.modules.licitacoes.schemas import TriagemRow

logger = logging.getLogger(__name__)

# --- Maquina de status (secao 5.1 do Projeto Tecnico) ----------------------

STATUS_NOVO_CAPTADO = "novo_captado"
STATUS_EM_ANALISE = "em_analise"
STATUS_APROVADO = "aprovado"
STATUS_REJEITADO = "rejeitado"
STATUS_PROCESSANDO_ANEXOS = "processando_anexos"
STATUS_COMPLETO = "completo"
STATUS_SEM_PLANILHA = "sem_planilha"
STATUS_ERRO_PORTAL = "erro_portal"
STATUS_ERRO_SHAREPOINT = "erro_sharepoint"

STATUS_VALIDOS: frozenset[str] = frozenset(
    {
        STATUS_NOVO_CAPTADO,
        STATUS_EM_ANALISE,
        STATUS_APROVADO,
        STATUS_REJEITADO,
        STATUS_PROCESSANDO_ANEXOS,
        STATUS_COMPLETO,
        STATUS_SEM_PLANILHA,
        STATUS_ERRO_PORTAL,
        STATUS_ERRO_SHAREPOINT,
    }
)

# Decisoes registradas na trilha (`licitacoes_decisoes_triagem.decisao`).
DECISAO_APROVADO = "aprovado"
DECISAO_REJEITADO = "rejeitado"
DECISAO_OBSERVACAO = "observacao"

# De onde se pode ir para onde. So `rejeitado` e terminal; os `erro_*`
# e `sem_planilha` permitem reprocessar (Squad 2 re-dispara), e `completo`
# tambem permite reprocesso (re-identificacao de planilha apos marcacao
# manual da analista).
TRANSICOES_VALIDAS: dict[str, frozenset[str]] = {
    STATUS_NOVO_CAPTADO: frozenset(
        {STATUS_EM_ANALISE, STATUS_APROVADO, STATUS_REJEITADO}
    ),
    STATUS_EM_ANALISE: frozenset(
        {STATUS_APROVADO, STATUS_REJEITADO, STATUS_NOVO_CAPTADO}
    ),
    STATUS_APROVADO: frozenset(
        {STATUS_PROCESSANDO_ANEXOS, STATUS_ERRO_PORTAL, STATUS_ERRO_SHAREPOINT}
    ),
    STATUS_PROCESSANDO_ANEXOS: frozenset(
        {
            STATUS_COMPLETO,
            STATUS_SEM_PLANILHA,
            STATUS_ERRO_PORTAL,
            STATUS_ERRO_SHAREPOINT,
        }
    ),
    STATUS_ERRO_PORTAL: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_ERRO_SHAREPOINT: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_SEM_PLANILHA: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_REJEITADO: frozenset(),
    STATUS_COMPLETO: frozenset({STATUS_PROCESSANDO_ANEXOS}),
}


class TransicaoInvalidaError(ValueError):
    """Transicao de status nao permitida pela maquina de triagem."""

    def __init__(self, atual: str, novo: str) -> None:
        self.atual = atual
        self.novo = novo
        super().__init__(
            f"Transicao de triagem invalida: {atual!r} -> {novo!r}"
        )


def validar_transicao(atual: str, novo: str) -> None:
    """Levanta `TransicaoInvalidaError` se `atual -> novo` nao for permitido."""
    if atual not in STATUS_VALIDOS or novo not in STATUS_VALIDOS:
        raise TransicaoInvalidaError(atual, novo)
    if novo not in TRANSICOES_VALIDAS[atual]:
        raise TransicaoInvalidaError(atual, novo)


_AUDIT_RESOURCE = "licitacoes.triagem"

MOTIVO_REJEICAO_MIN_CHARS = 5

# --- Celery dispatch (Squad 2) ---------------------------------------------
# Singleton por processo, mesmo racional de
# `manutencao_frota.service.get_celery_dispatcher` (evita vazar pool de
# conexoes broker a cada aprovacao).
_celery_dispatcher: Any | None = None


def _get_celery_dispatcher() -> Any:
    global _celery_dispatcher
    if _celery_dispatcher is None:
        from celery import Celery

        _celery_dispatcher = Celery(broker=get_settings().redis_url)
    return _celery_dispatcher


def reset_celery_dispatcher() -> Any | None:
    """Limpa o singleton (testes / lifespan)."""
    global _celery_dispatcher
    prev = _celery_dispatcher
    _celery_dispatcher = None
    return prev


def _enqueue_processamento(licitacao_id: int) -> None:
    """Despacha o processamento da Squad 2 se a flag estiver ligada.

    A task pode nao existir ainda (Squad 2 em desenvolvimento) e o broker
    pode estar fora -- em NENHUM caso o aprovar pode falhar por isso.
    """
    if not get_settings().captador_auto_process:
        return
    try:
        _get_celery_dispatcher().send_task(
            "worker.tasks.licitacoes.processar_edital_aprovado",
            args=[licitacao_id],
            queue="licitacoes",
        )
    except Exception:  # noqa: BLE001 -- dispatch e best-effort por contrato
        logger.exception(
            "dispatch de worker.tasks.licitacoes.processar_edital_aprovado falhou "
            "(licitacao_id=%s); processar manualmente ou reaprovar",
            licitacao_id,
        )


# --- Servico ----------------------------------------------------------------


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int,
    actor: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id),
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )


async def _get_licitacao_or_raise(
    db: AsyncSession, licitacao_id: int
) -> Licitacao:
    lic = await db.scalar(select(Licitacao).where(Licitacao.id == licitacao_id))
    if lic is None:
        raise LookupError(f"Licitacao {licitacao_id} nao encontrada")
    return lic


async def _decidir(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    decisao: str,
    acao: str,
    novo_status: str,
    observacao: str | None,
) -> DecisaoTriagem:
    lic = await _get_licitacao_or_raise(db, licitacao_id)
    validar_transicao(lic.status_triagem, novo_status)
    status_anterior = lic.status_triagem
    lic.status_triagem = novo_status
    row = DecisaoTriagem(
        licitacao_id=lic.id,
        decisao=decisao,
        observacao=observacao,
        usuario_email=usuario_email,
    )
    db.add(row)
    await _record_audit(
        db,
        action=f"triagem.{acao}",
        resource_id=lic.id,
        actor=usuario_email,
        metadata={
            "status_anterior": status_anterior,
            "status_novo": novo_status,
            "observacao": observacao,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def aprovar(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    observacao: str | None = None,
) -> DecisaoTriagem:
    """Aprova o edital na triagem. Dispara a Squad 2 se a flag permitir."""
    row = await _decidir(
        db,
        licitacao_id=licitacao_id,
        usuario_email=usuario_email,
        decisao=DECISAO_APROVADO,
        acao="aprovar",
        novo_status=STATUS_APROVADO,
        observacao=observacao.strip() if observacao else None,
    )
    _enqueue_processamento(licitacao_id)
    return row


async def rejeitar(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    observacao: str,
) -> DecisaoTriagem:
    """Rejeita o edital. Motivo obrigatorio (secao 7.1 do Projeto Tecnico)."""
    motivo = (observacao or "").strip()
    if len(motivo) < MOTIVO_REJEICAO_MIN_CHARS:
        raise ValueError(
            "Rejeicao exige motivo com pelo menos "
            f"{MOTIVO_REJEICAO_MIN_CHARS} caracteres"
        )
    return await _decidir(
        db,
        licitacao_id=licitacao_id,
        usuario_email=usuario_email,
        decisao=DECISAO_REJEITADO,
        acao="rejeitar",
        novo_status=STATUS_REJEITADO,
        observacao=motivo,
    )


async def registrar_observacao(
    db: AsyncSession,
    *,
    licitacao_id: int,
    usuario_email: str,
    observacao: str,
) -> DecisaoTriagem:
    """Anota uma observacao sem decidir. `novo_captado` vira `em_analise`."""
    texto = (observacao or "").strip()
    if not texto:
        raise ValueError("Observacao nao pode ser vazia")
    lic = await _get_licitacao_or_raise(db, licitacao_id)
    status_anterior = lic.status_triagem
    if lic.status_triagem == STATUS_NOVO_CAPTADO:
        lic.status_triagem = STATUS_EM_ANALISE
    row = DecisaoTriagem(
        licitacao_id=lic.id,
        decisao=DECISAO_OBSERVACAO,
        observacao=texto,
        usuario_email=usuario_email,
    )
    db.add(row)
    await _record_audit(
        db,
        action="triagem.observacao",
        resource_id=lic.id,
        actor=usuario_email,
        metadata={
            "status_anterior": status_anterior,
            "status_novo": lic.status_triagem,
            "observacao": texto,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def listar_decisoes(
    db: AsyncSession, *, licitacao_id: int
) -> list[DecisaoTriagem]:
    """Historico de decisoes, mais recente primeiro."""
    await _get_licitacao_or_raise(db, licitacao_id)
    rows = await db.scalars(
        select(DecisaoTriagem)
        .where(DecisaoTriagem.licitacao_id == licitacao_id)
        .order_by(DecisaoTriagem.created_at.desc(), DecisaoTriagem.id.desc())
    )
    return list(rows)


# --- Aba de Triagem consolidada (Captador Squad 2) --------------------------
#
# Uma query paginada sobre `licitacoes` + 4 lookups em lote (edital, pasta,
# planilha principal, ultima observacao) -- sem N+1 por linha. Criterio de
# UAT do cliente: os links devem abrir pasta e planilha diretamente, sem
# navegacao manual.


def _link_portal(lic: Licitacao) -> str | None:
    if lic.orgao_cnpj and lic.ano_compra and lic.sequencial_compra:
        return (
            "https://pncp.gov.br/app/editais/"
            f"{lic.orgao_cnpj}/{lic.ano_compra}/{lic.sequencial_compra}"
        )
    return None


async def montar_triagem(
    db: AsyncSession,
    *,
    status: str | None = None,
    uf: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TriagemRow], int]:
    base = select(Licitacao)
    if status:
        base = base.where(Licitacao.status_triagem == status)
    if uf:
        base = base.where(Licitacao.uf_sigla == uf.upper())

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    licitacoes = list(
        (
            await db.execute(
                base.order_by(Licitacao.data_publicacao_pncp.desc().nullslast())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    if not licitacoes:
        return [], int(total)

    ids = [lic.id for lic in licitacoes]

    editais = {
        e.licitacao_id: e
        for e in (
            await db.execute(select(Edital).where(Edital.licitacao_id.in_(ids)))
        ).scalars()
    }
    pastas = {
        p.licitacao_id: p
        for p in (
            await db.execute(
                select(PastaProjeto).where(PastaProjeto.licitacao_id.in_(ids))
            )
        ).scalars()
    }
    principais = {
        p.licitacao_id: p
        for p in (
            await db.execute(
                select(PlanilhaOrcamentaria).where(
                    PlanilhaOrcamentaria.licitacao_id.in_(ids),
                    PlanilhaOrcamentaria.principal,
                )
            )
        ).scalars()
    }
    # Ultima observacao nao-nula por licitacao (id mais alto = mais recente).
    observacoes: dict[int, str] = {}
    decisoes = (
        await db.execute(
            select(DecisaoTriagem)
            .where(
                DecisaoTriagem.licitacao_id.in_(ids),
                DecisaoTriagem.observacao.is_not(None),
            )
            .order_by(DecisaoTriagem.id)
        )
    ).scalars()
    for d in decisoes:
        observacoes[d.licitacao_id] = d.observacao

    rows: list[TriagemRow] = []
    for lic in licitacoes:
        edital = editais.get(lic.id)
        pasta = pastas.get(lic.id)
        planilha = principais.get(lic.id)
        rows.append(
            TriagemRow(
                licitacao_id=lic.id,
                status_triagem=lic.status_triagem,
                uf_sigla=lic.uf_sigla,
                municipio_nome=lic.municipio_nome,
                orgao_razao_social=lic.orgao_razao_social,
                objeto_compra=lic.objeto_compra,
                modalidade_nome=lic.modalidade_nome,
                valor_total_estimado=lic.valor_total_estimado,
                data_publicacao_pncp=lic.data_publicacao_pncp,
                link_portal=_link_portal(lic),
                link_pasta=pasta.link_pasta if pasta else None,
                link_planilha=planilha.link if planilha else None,
                planilha_nome=planilha.nome_arquivo if planilha else None,
                anexos_count=edital.anexos_count if edital else 0,
                observacao=observacoes.get(lic.id),
            )
        )
    return rows, int(total)
