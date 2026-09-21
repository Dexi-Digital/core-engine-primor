"""Regras da caixa de notificacoes.

Toda consulta leva `destinatario`. Nao existe "listar todas e filtrar
na tela": boletim de licitacao carrega valor de contrato e alvo
comercial, e o filtro que protege isso tem de estar na query.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notificacoes.models import Notificacao


async def criar_notificacao(
    db: AsyncSession,
    *,
    destinatario: str,
    titulo: str,
    corpo: str,
    categoria: str,
    link: str | None = None,
    chave_idempotencia: str | None = None,
) -> Notificacao:
    """Cria, ou devolve a existente quando a chave repete.

    Devolver a existente (em vez de erro) deixa o despacho do beat
    ser reexecutado sem tratamento especial -- rodar de novo e
    inofensivo, que e a propriedade que queremos de qualquer job
    agendado.
    """
    destinatario = destinatario.strip().lower()

    if chave_idempotencia:
        existente = (
            await db.execute(
                select(Notificacao)
                .where(Notificacao.destinatario == destinatario)
                .where(Notificacao.chave_idempotencia == chave_idempotencia)
            )
        ).scalar_one_or_none()
        if existente is not None:
            return existente

    n = Notificacao(
        destinatario=destinatario,
        titulo=titulo,
        corpo=corpo,
        categoria=categoria,
        link=link,
        chave_idempotencia=chave_idempotencia,
    )
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return n


async def listar(
    db: AsyncSession,
    *,
    destinatario: str,
    apenas_nao_lidas: bool = False,
    limite: int = 50,
) -> list[Notificacao]:
    q = select(Notificacao).where(
        Notificacao.destinatario == destinatario.strip().lower()
    )
    if apenas_nao_lidas:
        q = q.where(Notificacao.lida_em.is_(None))
    q = q.order_by(Notificacao.created_at.desc(), Notificacao.id.desc())
    return list((await db.execute(q.limit(limite))).scalars().all())


async def contar_nao_lidas(db: AsyncSession, *, destinatario: str) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Notificacao)
            .where(Notificacao.destinatario == destinatario.strip().lower())
            .where(Notificacao.lida_em.is_(None))
        )
    ).scalar_one()


async def marcar_lida(
    db: AsyncSession, *, destinatario: str, notificacao_id: int
) -> bool:
    """`False` quando a notificacao nao e dessa pessoa (ou nao existe).

    O filtro por destinatario e a autorizacao: sem ele, conhecer o id
    bastaria para mexer na caixa de outro usuario.
    """
    n = (
        await db.execute(
            select(Notificacao)
            .where(Notificacao.id == notificacao_id)
            .where(Notificacao.destinatario == destinatario.strip().lower())
        )
    ).scalar_one_or_none()
    if n is None:
        return False
    if n.lida_em is None:
        n.lida_em = datetime.now(UTC)
        await db.commit()
    return True


async def marcar_todas_lidas(db: AsyncSession, *, destinatario: str) -> int:
    nao_lidas = await listar(
        db, destinatario=destinatario, apenas_nao_lidas=True, limite=1000
    )
    agora = datetime.now(UTC)
    for n in nao_lidas:
        n.lida_em = agora
    if nao_lidas:
        await db.commit()
    return len(nao_lidas)
