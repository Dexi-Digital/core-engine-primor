"""Pull do EasyJur e as consultas da tela.

O pull e somente leitura e idempotente: upsert por `easyjur_id`, tanto
em processo quanto em andamento. Rodar N vezes nao duplica.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.easyjur import parser
from app.modules.juridico.models import (
    SOURCES_SYNC,
    SYNC_EM_ANDAMENTO,
    SYNC_ERRO,
    SYNC_OK,
    Andamento,
    Processo,
    SyncLog,
)
from app.modules.manutencao_frota.service import get_celery_dispatcher
from app.modules.obras.models import Obra

logger = logging.getLogger(__name__)

# 453 processos = 10 paginas. O teto existe para um bug de paginacao do
# lado deles nao virar laco infinito contra um sistema de terceiro.
_MAX_PAGINAS = 60

# Uma carga leva ~2,5 min. Depois disto, "em_andamento" deixa de valer:
# o worker provavelmente nao existe neste ambiente (ou morreu no meio),
# e o botao volta a funcionar -- com a tela avisando.
SYNC_TRAVADO_APOS = timedelta(minutes=30)

TASK_PULL = "worker.tasks.juridico.pull_easyjur"
FILA_PULL = "financeiro"  # fila EXISTENTE: o CMD do worker lista as filas com -Q

# Campos que o escritorio preenche por excecao (medido em 19/09/2026:
# tipo_acao 15%, risco 11%, resultado 8%, fase_atual 6%).
CAMPOS_ESPARSOS = ("tipo_acao", "risco", "resultado", "fase_atual")


class ClienteEasyjur(Protocol):
    async def listar_processos(self, page: int = 1) -> str: ...
    async def exportar_andamentos_csv(self) -> bytes: ...


@dataclass
class ResultadoSync:
    processos: int
    andamentos: int
    total_declarado: int | None
    divergencia: bool


async def _coletar_processos(
    client: ClienteEasyjur,
) -> tuple[list[parser.ProcessoBruto], int | None]:
    coletados: dict[int, parser.ProcessoBruto] = {}
    declarado: int | None = None
    for page in range(1, _MAX_PAGINAS + 1):
        html = await client.listar_processos(page=page)
        if declarado is None:
            declarado = parser.total_registros(html)
        pagina = parser.parse_processos(html)
        if not pagina:
            break
        for p in pagina:
            coletados[p.easyjur_id] = p
        if declarado is not None and len(coletados) >= declarado:
            break
    return list(coletados.values()), declarado


async def sincronizar(
    db: AsyncSession, client: ClienteEasyjur, *, source: str
) -> ResultadoSync:
    if source not in SOURCES_SYNC:
        raise ValueError(f"source invalido: {source!r}")

    brutos, declarado = await _coletar_processos(client)
    andamentos_brutos = parser.parse_andamentos_csv(
        await client.exportar_andamentos_csv()
    )

    # --- processos ---
    obras = {
        o.codigo: o.id for o in (await db.execute(select(Obra))).scalars().all()
    }
    existentes = {
        p.easyjur_id: p for p in (await db.execute(select(Processo))).scalars().all()
    }
    for b in brutos:
        p = existentes.get(b.easyjur_id)
        if p is None:
            p = Processo(easyjur_id=b.easyjur_id)
            db.add(p)
            existentes[b.easyjur_id] = p
        for campo in (
            "numero_cnj", "status", "area", "tribunal", "instancia", "comarca",
            "titulo", "cliente", "contrario", "tipo_acao", "risco", "fase_atual",
            "resultado", "codigo_obra",
        ):
            setattr(p, campo, getattr(b, campo))
        p.grupos = " | ".join(b.grupos) or None
        # Nao inventamos obra: codigo sem cadastro fica com obra_id nulo.
        p.obra_id = _obra_id(obras, b.codigo_obra)
    await db.flush()

    # --- andamentos ---
    por_cnj = {p.numero_cnj: p.id for p in existentes.values() if p.numero_cnj}
    ja = {
        a.easyjur_id: a for a in (await db.execute(select(Andamento))).scalars().all()
    }
    for b in andamentos_brutos:
        a = ja.get(b.easyjur_id)
        if a is None:
            a = Andamento(easyjur_id=b.easyjur_id, numero_cnj=b.numero_cnj)
            db.add(a)
            ja[b.easyjur_id] = a
        a.numero_cnj = b.numero_cnj
        # Processo desconhecido: o andamento entra SEM vinculo. Descartar
        # perderia movimentacao real; inventar o processo seria pior.
        a.processo_id = por_cnj.get(b.numero_cnj)
        a.tipo, a.status, a.descricao, a.data = b.tipo, b.status, b.descricao, b.data

    # A base e viva e cresce durante o pull (medido: 12.109 -> 12.121 na
    # mesma sessao), entao coletar A MAIS que o declarado e normal. O
    # que nao pode passar em silencio e coletar a MENOS.
    divergencia = declarado is not None and len(brutos) < declarado
    if divergencia:
        logger.warning(
            "easyjur.divergencia coletados=%s declarado=%s", len(brutos), declarado
        )

    resultado = ResultadoSync(
        processos=len(brutos),
        andamentos=len(andamentos_brutos),
        total_declarado=declarado,
        divergencia=divergencia,
    )
    await _registrar(db, source, resultado)
    await db.commit()
    return resultado


def _obra_id(obras: dict[str, int], codigo: str | None) -> int | None:
    if not codigo:
        return None
    # O Tangerino guarda com zero a esquerda ("003"); o EasyJur nao.
    return obras.get(codigo) or obras.get(codigo.zfill(3))


async def _log_de_hoje(db: AsyncSession, source: str) -> SyncLog:
    hoje = date.today()
    log = (
        await db.execute(
            select(SyncLog)
            .where(SyncLog.source == source)
            .where(SyncLog.janela == hoje)
        )
    ).scalar_one_or_none()
    if log is None:
        log = SyncLog(source=source, janela=hoje)
        db.add(log)
    return log


async def _registrar(db: AsyncSession, source: str, r: ResultadoSync) -> None:
    log = await _log_de_hoje(db, source)
    log.processos = r.processos
    log.andamentos = r.andamentos
    log.total_declarado = r.total_declarado
    log.divergencia = r.divergencia
    log.status = SYNC_OK
    log.erro = None


# --- estado da carga (o que a tela mostra) ----------------------------------


async def marcar_inicio(db: AsyncSession, *, source: str) -> None:
    log = await _log_de_hoje(db, source)
    log.status = SYNC_EM_ANDAMENTO
    log.iniciado_em = datetime.now(UTC)
    log.erro = None
    await db.commit()


async def marcar_erro(db: AsyncSession, *, source: str, mensagem: str) -> None:
    log = await _log_de_hoje(db, source)
    log.status = SYNC_ERRO
    log.erro = mensagem[:1024]
    await db.commit()


async def em_andamento(db: AsyncSession, *, source: str) -> bool:
    log = (
        await db.execute(
            select(SyncLog)
            .where(SyncLog.source == source)
            .where(SyncLog.status == SYNC_EM_ANDAMENTO)
            .order_by(SyncLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if log is None or log.iniciado_em is None:
        return False
    inicio = log.iniciado_em
    if inicio.tzinfo is None:  # sqlite devolve naive
        inicio = inicio.replace(tzinfo=UTC)
    return datetime.now(UTC) - inicio < SYNC_TRAVADO_APOS


class CargaJaEmAndamento(RuntimeError):
    pass


class FilaIndisponivel(RuntimeError):
    pass


async def enfileirar_carga(db: AsyncSession, *, source: str = "manual") -> None:
    """Pede ao worker para rodar o pull. Nao toca no EasyJur daqui.

    Marca "em_andamento" ANTES de enfileirar: se a fila falhar, o log
    ja existe para receber o erro -- e a tela mostra o motivo em vez de
    recarregar igual.
    """
    if await em_andamento(db, source=source):
        raise CargaJaEmAndamento("ja ha uma carga em andamento")
    await marcar_inicio(db, source=source)
    try:
        get_celery_dispatcher().send_task(TASK_PULL, args=[source], queue=FILA_PULL)
    except Exception as exc:  # noqa: BLE001 -- kombu/redis levantam tipos variados
        await marcar_erro(
            db, source=source, mensagem=f"fila do worker indisponivel: {exc}"
        )
        raise FilaIndisponivel(str(exc)) from exc


# --- consultas da tela ------------------------------------------------------


async def listar_processos(
    db: AsyncSession,
    *,
    status: str | None = None,
    area: str | None = None,
    busca: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> tuple[list[Processo], int]:
    q = select(Processo)
    if status:
        q = q.where(Processo.status == status)
    if area:
        q = q.where(Processo.area == area)
    if busca:
        termo = f"%{busca.strip()}%"
        q = q.where(
            Processo.numero_cnj.ilike(termo)
            | Processo.contrario.ilike(termo)
            | Processo.cliente.ilike(termo)
        )
    total = (
        await db.execute(select(func.count()).select_from(q.subquery()))
    ).scalar_one()
    linhas = (
        await db.execute(
            q.order_by(Processo.easyjur_id.desc()).limit(limite).offset(offset)
        )
    ).scalars().all()
    return list(linhas), total


async def ultimos_andamentos(db: AsyncSession, *, limite: int = 30) -> list[Andamento]:
    return list(
        (
            await db.execute(
                select(Andamento)
                .order_by(Andamento.data.desc().nulls_last(), Andamento.id.desc())
                .limit(limite)
            )
        ).scalars().all()
    )


async def _contagem(db: AsyncSession, coluna: Any) -> dict[str, int]:
    res = await db.execute(
        select(coluna, func.count()).where(coluna.is_not(None)).group_by(coluna)
    )
    return dict(sorted(((k, n) for k, n in res.all()), key=lambda kv: -kv[1]))


async def resumo(db: AsyncSession) -> dict[str, Any]:
    total = (
        await db.execute(select(func.count()).select_from(Processo))
    ).scalar_one()

    esparsos: dict[str, dict[str, Any]] = {}
    for campo in CAMPOS_ESPARSOS:
        coluna = getattr(Processo, campo)
        valores = await _contagem(db, coluna)
        # O denominador vai JUNTO, sempre. "Risco remoto em 96%" seria
        # falso onde o real e "52 de 54 preenchidos, de 453".
        esparsos[campo] = {
            "preenchidos": sum(valores.values()),
            "total": total,
            "valores": valores,
        }

    ultimo = (
        await db.execute(select(SyncLog).order_by(SyncLog.executado_em.desc()).limit(1))
    ).scalar_one_or_none()

    return {
        "total": total,
        "andamentos": (
            await db.execute(select(func.count()).select_from(Andamento))
        ).scalar_one(),
        "por_status": await _contagem(db, Processo.status),
        "por_area": await _contagem(db, Processo.area),
        "por_tribunal": await _contagem(db, Processo.tribunal),
        "com_obra": (
            await db.execute(
                select(func.count())
                .select_from(Processo)
                .where(Processo.codigo_obra.is_not(None))
            )
        ).scalar_one(),
        "campos_esparsos": esparsos,
        "ultimo_sync": (
            {
                "executado_em": ultimo.executado_em.isoformat()
                if ultimo.executado_em
                else None,
                "source": ultimo.source,
                "processos": ultimo.processos,
                "andamentos": ultimo.andamentos,
                "total_declarado": ultimo.total_declarado,
                "divergencia": ultimo.divergencia,
                "status": ultimo.status,
                "erro": ultimo.erro,
                "iniciado_em": ultimo.iniciado_em.isoformat()
                if ultimo.iniciado_em
                else None,
            }
            if ultimo
            else None
        ),
    }
