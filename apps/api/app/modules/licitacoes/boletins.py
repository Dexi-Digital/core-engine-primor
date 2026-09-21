"""Boletins de licitacao (D.3) -- notificacao na plataforma.

Um `SavedQuery` representa um filtro cadastrado por um usuario (UF + modalidade
+ termo de busca). Tres vezes ao dia, o scheduler dispara `dispatch_boletins`
que, para cada query ativa:

1. carrega o ultimo envio (`BoletimLog`) e pega `last_licitacao_id`;
2. busca licitacoes NOVAS desde entao que batem com os filtros;
3. se houver 1 ou mais, cria uma NOTIFICACAO na plataforma por
   destinatario (desde 21/09/2026; antes era email via Resend);
4. registra um `BoletimLog` (sucesso, vazio ou falha) com o novo cursor.

Esse servico NAO depende do FastAPI -- o Celery worker importa diretamente.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from html import escape
from urllib.parse import quote

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.licitacoes.models import BoletimLog, Licitacao, SavedQuery
from app.modules.licitacoes.schemas import (
    BoletimDispatchResult,
    BoletimDispatchSummary,
)
from app.modules.notificacoes.models import CATEGORIA_LICITACOES
from app.modules.notificacoes.service import criar_notificacao

logger = logging.getLogger(__name__)

DIGEST_MAX_ITEMS = 20


async def _last_cursor(db: AsyncSession, saved_query_id: int) -> int | None:
    stmt = (
        select(BoletimLog.last_licitacao_id)
        .where(BoletimLog.saved_query_id == saved_query_id)
        .where(BoletimLog.status == "sent")
        .order_by(desc(BoletimLog.sent_at))
        .limit(1)
    )
    return await db.scalar(stmt)


async def _new_licitacoes_for_query(
    db: AsyncSession,
    query: SavedQuery,
    since_id: int | None,
) -> list[Licitacao]:
    stmt = select(Licitacao).order_by(Licitacao.id.asc())

    if since_id is not None:
        stmt = stmt.where(Licitacao.id > since_id)
    if query.uf:
        stmt = stmt.where(Licitacao.uf_sigla == query.uf.upper())
    if query.modalidade:
        stmt = stmt.where(Licitacao.modalidade_nome.ilike(f"%{query.modalidade}%"))
    if query.orgao_cnpj:
        stmt = stmt.where(Licitacao.orgao_cnpj == query.orgao_cnpj)
    if query.search:
        stmt = stmt.where(Licitacao.objeto_compra.ilike(f"%{query.search}%"))

    stmt = stmt.limit(DIGEST_MAX_ITEMS)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


def render_digest_html(
    query: SavedQuery,
    licitacoes: Iterable[Licitacao],
    *,
    public_base_url: str,
) -> str:
    """Render the digest email. Plain HTML, inline styles for email clients."""
    filter_parts: list[str] = []
    if query.uf:
        filter_parts.append(f"UF={escape(query.uf)}")
    if query.modalidade:
        filter_parts.append(f"modalidade={escape(query.modalidade)}")
    if query.search:
        filter_parts.append(f'busca="{escape(query.search)}"')
    if query.orgao_cnpj:
        filter_parts.append(f"CNPJ={escape(query.orgao_cnpj)}")
    filter_line = " · ".join(filter_parts) if filter_parts else "sem filtros"

    rows_html: list[str] = []
    items = list(licitacoes)
    for lic in items:
        objeto = escape((lic.objeto_compra or "—")[:240])
        modalidade = escape(lic.modalidade_nome or "—")
        orgao = escape(lic.orgao_razao_social or lic.orgao_cnpj or "—")
        uf = escape(lic.uf_sigla or "—")
        valor = (
            f"R$ {float(lic.valor_total_estimado):,.2f}".replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
            if lic.valor_total_estimado is not None
            else "—"
        )
        data_pub = (
            lic.data_publicacao_pncp.strftime("%d/%m/%Y")
            if isinstance(lic.data_publicacao_pncp, datetime)
            else "—"
        )
        rows_html.append(
            f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
              <td style="padding: 8px; font-size: 13px;">{data_pub}</td>
              <td style="padding: 8px; font-size: 13px;">{uf}</td>
              <td style="padding: 8px; font-size: 13px;">{modalidade}</td>
              <td style="padding: 8px; font-size: 13px;">{objeto}</td>
              <td style="padding: 8px; font-size: 13px;">{orgao}</td>
              <td style="padding: 8px; font-size: 13px; text-align: right;">{valor}</td>
            </tr>
            """.strip()
        )

    dashboard_url = f"{public_base_url.rstrip('/')}/licitacoes"
    # URL-encode UF / search -- search frequently contains spaces, accents
    # or ampersands (pt-BR procurement text) and would otherwise break links.
    if query.uf:
        dashboard_url += f"?uf={quote(query.uf, safe='')}"
    if query.search:
        sep = "&" if "?" in dashboard_url else "?"
        dashboard_url += f"{sep}search={quote(query.search, safe='')}"

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 720px; margin: 0 auto; color: #0f172a;">
      <h2 style="margin: 0 0 4px;">Boletim: {escape(query.nome)}</h2>
      <p style="margin: 0 0 16px; color: #475569; font-size: 13px;">
        {len(items)} nova(s) licitação(ões) · Filtros: {filter_line}
      </p>
      <table style="width: 100%; border-collapse: collapse; border-top: 1px solid #e5e7eb;">
        <thead>
          <tr style="background: #f1f5f9; text-align: left;">
            <th style="padding: 8px; font-size: 12px;">Publicação</th>
            <th style="padding: 8px; font-size: 12px;">UF</th>
            <th style="padding: 8px; font-size: 12px;">Modalidade</th>
            <th style="padding: 8px; font-size: 12px;">Objeto</th>
            <th style="padding: 8px; font-size: 12px;">Órgão</th>
            <th style="padding: 8px; font-size: 12px; text-align: right;">Valor estimado</th>
          </tr>
        </thead>
        <tbody>{"".join(rows_html)}</tbody>
      </table>
      <p style="margin-top: 20px; font-size: 13px;">
        <a href="{escape(dashboard_url)}" style="color: #2563eb; text-decoration: none;">
          Abrir no Motor Central →
        </a>
      </p>
      <p style="margin-top: 24px; font-size: 11px; color: #94a3b8;">
        Você está recebendo este boletim porque cadastrou a query "{escape(query.nome)}"
        no Motor Central. Para parar de receber, desative a query no dashboard.
      </p>
    </div>
    """.strip()


def _resumo_texto(query: SavedQuery, licitacoes: list[Licitacao]) -> str:
    """Corpo da notificacao, em TEXTO.

    De proposito nao e o `render_digest_html`: o objeto vem do PNCP --
    texto de terceiro -- e o painel renderiza o corpo direto. Jogar HTML
    de terceiro numa tela autenticada seria abrir XSS para economizar
    formatacao.
    """
    linhas = [
        f"{len(licitacoes)} nova(s) oportunidade(s) para a busca "
        f"\u201c{query.nome}\u201d."
    ]
    for lic in licitacoes[:5]:
        objeto = _so_texto(lic.objeto_compra or "(sem objeto)")
        uf = lic.uf_sigla or "--"
        linhas.append(f"- [{uf}] {objeto[:120]}")
    if len(licitacoes) > 5:
        linhas.append(f"... e mais {len(licitacoes) - 5}.")
    return "\n".join(linhas)


def _so_texto(valor: str) -> str:
    """Remove marcacao do texto vindo do PNCP.

    Nao e sanitizacao de HTML sofisticada porque nao precisa ser: aqui
    nada de `<` pode sair, entao a regra e simplesmente descartar tudo
    entre sinais de menor/maior e os proprios sinais.
    """
    saida, dentro = [], False
    for ch in valor:
        if ch == "<":
            dentro = True
        elif ch == ">":
            dentro = False
        elif not dentro:
            saida.append(ch)
    return " ".join("".join(saida).split())


def _link_da_query(query: SavedQuery) -> str:
    """Para onde o clique leva: a lista ja filtrada pela busca salva.

    Notificacao sem link obriga a pessoa a reconstruir o filtro na mao
    para descobrir o que mudou.
    """
    from urllib.parse import urlencode

    filtros = {}
    if query.uf:
        filtros["uf"] = query.uf
    if query.search:
        filtros["search"] = query.search
    return "/licitacoes" + (f"?{urlencode(filtros)}" if filtros else "")


async def dispatch_boletins(
    db: AsyncSession,
    *,
    public_base_url: str | None = None,
    saved_query_ids: list[int] | None = None,
) -> BoletimDispatchSummary:
    """Despacha boletins pendentes como NOTIFICACAO NA PLATAFORMA.

    Ate 21/09/2026 isto mandava email via Resend. O cliente pediu para
    nao enviar email e mostrar o aviso dentro do sistema -- entao o
    `ResendClient` saiu da assinatura, e nao apenas do corpo: deixar o
    parametro deixaria religar o envio por engano.

    O que NAO mudou, porque ja estava certo: o cursor por saved query
    (`last_licitacao_id`) e o isolamento de falha -- uma query que
    quebra nao aborta as outras.
    """
    settings = get_settings()
    public_base_url = public_base_url or settings.public_base_url

    stmt = select(SavedQuery).where(SavedQuery.active.is_(True))
    if saved_query_ids:
        stmt = stmt.where(SavedQuery.id.in_(saved_query_ids))
    queries = (await db.execute(stmt)).scalars().all()

    results: list[BoletimDispatchResult] = []
    sent = 0
    skipped_empty = 0
    failed = 0

    for query in queries:
        since_id = await _last_cursor(db, query.id)
        licitacoes = await _new_licitacoes_for_query(db, query, since_id)

        if not licitacoes:
            log = BoletimLog(
                saved_query_id=query.id,
                licitacoes_count=0,
                last_licitacao_id=since_id,
                status="skipped_empty",
            )
            db.add(log)
            skipped_empty += 1
            results.append(
                BoletimDispatchResult(
                    saved_query_id=query.id,
                    licitacoes_count=0,
                    last_licitacao_id=since_id,
                    status="skipped_empty",
                )
            )
            continue

        last_id = max(lic.id for lic in licitacoes)
        titulo = f"{len(licitacoes)} nova(s) licitação(ões) — {query.nome}"
        corpo = _resumo_texto(query, licitacoes)
        link = _link_da_query(query)
        # A chave inclui o cursor: mesma query com o mesmo topo nao gera
        # notificacao nova, e o beat rodando 3x/dia fica inofensivo.
        chave = f"boletim:{query.id}:{last_id}"

        try:
            for destinatario in query.recipients:
                await criar_notificacao(
                    db,
                    destinatario=destinatario,
                    titulo=titulo,
                    corpo=corpo,
                    categoria=CATEGORIA_LICITACOES,
                    link=link,
                    chave_idempotencia=chave,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("boletim %s failed: %s", query.id, exc, exc_info=False)
            log = BoletimLog(
                saved_query_id=query.id,
                licitacoes_count=len(licitacoes),
                last_licitacao_id=since_id,  # do NOT advance cursor on failure
                status="failed",
                error_message=str(exc)[:1024],
            )
            db.add(log)
            failed += 1
            results.append(
                BoletimDispatchResult(
                    saved_query_id=query.id,
                    licitacoes_count=len(licitacoes),
                    last_licitacao_id=since_id,
                    status="failed",
                    error_message=str(exc)[:1024],
                )
            )
            continue

        message_id = None  # nao ha mais envio de email
        log = BoletimLog(
            saved_query_id=query.id,
            licitacoes_count=len(licitacoes),
            last_licitacao_id=last_id,
            resend_message_id=message_id,
            status="sent",
        )
        db.add(log)
        sent += 1
        results.append(
            BoletimDispatchResult(
                saved_query_id=query.id,
                licitacoes_count=len(licitacoes),
                last_licitacao_id=last_id,
                status="sent",
                resend_message_id=message_id,
            )
        )

    await db.commit()

    return BoletimDispatchSummary(
        total_queries=len(queries),
        sent=sent,
        skipped_empty=skipped_empty,
        failed=failed,
        results=results,
    )


# --- CRUD ---


async def create_saved_query(
    db: AsyncSession,
    *,
    nome: str,
    user_email: str,
    recipients: list[str],
    uf: str | None = None,
    modalidade: str | None = None,
    search: str | None = None,
    orgao_cnpj: str | None = None,
    active: bool = True,
) -> SavedQuery:
    row = SavedQuery(
        nome=nome,
        user_email=user_email,
        recipients=recipients,
        uf=uf.upper() if uf else None,
        modalidade=modalidade,
        search=search,
        orgao_cnpj=orgao_cnpj,
        active=active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_saved_queries(
    db: AsyncSession, *, user_email: str | None = None
) -> list[SavedQuery]:
    stmt = select(SavedQuery).order_by(SavedQuery.created_at.desc())
    if user_email:
        stmt = stmt.where(SavedQuery.user_email == user_email)
    return list((await db.execute(stmt)).scalars().all())


async def delete_saved_query(db: AsyncSession, saved_query_id: int) -> bool:
    row = await db.get(SavedQuery, saved_query_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True
