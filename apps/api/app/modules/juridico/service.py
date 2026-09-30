"""Pull do EasyJur e as consultas da tela.

O pull e somente leitura e idempotente: upsert por `easyjur_id`, tanto
em processo quanto em andamento. Rodar N vezes nao duplica.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.config import get_settings
from app.integrations.easyjur import parser
from app.integrations.easyjur.client import EasyjurError, EasyjurLoginRecusadoError
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
    iniciado = time.monotonic()
    logger.info("easyjur.processos.start max_paginas=%s", _MAX_PAGINAS)
    coletados: dict[int, parser.ProcessoBruto] = {}
    declarado: int | None = None
    for page in range(1, _MAX_PAGINAS + 1):
        html = await client.listar_processos(page=page)
        if declarado is None:
            declarado = parser.total_registros(html)
        pagina = parser.parse_processos(html)
        if not pagina and declarado is None:
            raise EasyjurError(
                "Resposta de processos sem registros nem total declarado; "
                "verifique a sessão e o layout do EasyJur"
            )
        if not pagina:
            break
        for p in pagina:
            coletados[p.easyjur_id] = p
        if declarado is not None and len(coletados) >= declarado:
            break
    logger.info(
        "easyjur.processos.done paginas=%s coletados=%s declarado=%s duracao_s=%.1f",
        page,
        len(coletados),
        declarado,
        time.monotonic() - iniciado,
    )
    return list(coletados.values()), declarado


async def sincronizar(
    db: AsyncSession, client: ClienteEasyjur, *, source: str
) -> ResultadoSync:
    if source not in SOURCES_SYNC:
        raise ValueError(f"source invalido: {source!r}")

    iniciou = time.monotonic()
    brutos, declarado = await _coletar_processos(client)
    logger.info("easyjur.andamentos_export.start")
    andamentos_brutos = parser.parse_andamentos_csv(
        await client.exportar_andamentos_csv()
    )
    logger.info(
        "easyjur.andamentos_export.done linhas=%s duracao_total_s=%.1f",
        len(andamentos_brutos),
        time.monotonic() - iniciou,
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


# --- trava de login entre execucoes -----------------------------------------
#
# O EasyJur bloqueia a conta apos 5 senhas erradas seguidas. O client ja
# se recusa a tentar perto do limite, mas esse contador vive na instancia
# e cada execucao cria uma nova: com a senha errada, o beat das 03:30 e
# cada clique em "Sincronizar agora" queimariam uma tentativa ate travar
# a conta de quem usa o EasyJur para trabalhar.
#
# Entao a recusa fica gravada no `audit_log` (sem migration, e a trilha
# de quem liberou fica de graca), com a IMPRESSAO da credencial -- HMAC
# da `secret_key`, nunca a senha. Enquanto a ultima linha for uma recusa
# com a mesma impressao, nao se tenta login. Destrava quando:
#   - a credencial muda (EASYJUR_EMAIL/EASYJUR_PASSWORD), ou
#   - um admin chama `POST /api/v1/juridico/login/liberar` -- o que
#     autoriza UMA nova tentativa: se falhar, trava de novo.
# Trocar a SECRET_KEY muda a impressao e tambem libera uma tentativa.

AUDIT_RESOURCE_LOGIN = "easyjur_login"
ACAO_LOGIN_RECUSADO = "easyjur.login_recusado"
ACAO_LOGIN_OK = "easyjur.login_ok"
ACAO_LOGIN_LIBERADO = "easyjur.login_liberado"
ACTOR_WORKER = "system:worker"

MSG_LOGIN_BLOQUEADO = (
    "Login recusado pelo EasyJur; nova tentativa só após trocar a credencial "
    "(EASYJUR_EMAIL/EASYJUR_PASSWORD) ou um admin liberar em "
    "POST /api/v1/juridico/login/liberar. A sincronização não tentou logar "
    "para não bloquear a conta (o EasyJur bloqueia após 5 erros seguidos)."
)


class LoginEasyjurBloqueado(RuntimeError):
    """Recusa nossa de tentar login: a credencial atual ja foi recusada."""


def impressao_credencial(email: str | None, password: str | None) -> str | None:
    """Impressao NAO reversivel da credencial. None sem credencial.

    HMAC com a `secret_key` em vez de sha256 puro: a impressao fica no
    `audit_log`, e sha256 sem chave de uma senha fraca cai em dicionario.
    """
    if not (email and password):
        return None
    chave = get_settings().secret_key.encode()
    msg = f"easyjur\0{email.strip().lower()}\0{password}".encode()
    return hmac.new(chave, msg, hashlib.sha256).hexdigest()


async def _ultimo_evento_login(db: AsyncSession) -> AuditLog | None:
    return (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.resource == AUDIT_RESOURCE_LOGIN)
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def login_bloqueado(db: AsyncSession, impressao: str | None) -> AuditLog | None:
    """A recusa que trava ESTA credencial, ou None se pode tentar."""
    if impressao is None:
        return None
    ultimo = await _ultimo_evento_login(db)
    if (
        ultimo is not None
        and ultimo.action == ACAO_LOGIN_RECUSADO
        and ultimo.resource_id == impressao
    ):
        return ultimo
    return None


def _auditar_login(
    db: AsyncSession, *, action: str, actor: str, impressao: str | None, **meta: Any
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=AUDIT_RESOURCE_LOGIN,
            resource_id=impressao,
            metadata_json=json.dumps(meta, ensure_ascii=False, default=str),
        )
    )


async def registrar_login_recusado(
    db: AsyncSession, impressao: str, exc: EasyjurLoginRecusadoError
) -> None:
    _auditar_login(
        db,
        action=ACAO_LOGIN_RECUSADO,
        actor=ACTOR_WORKER,
        impressao=impressao,
        mensagem=str(exc)[:500],
        tentativas_restantes=exc.tentativas_restantes,
    )
    await db.commit()
    logger.error(
        "easyjur.login_recusado_gravado tentativas_restantes=%s",
        exc.tentativas_restantes,
    )


async def registrar_login_ok(db: AsyncSession, impressao: str) -> None:
    """Fecha uma recusa anterior. Sem recusa aberta, nao grava nada --
    uma linha de auditoria por madrugada so faria ruido."""
    ultimo = await _ultimo_evento_login(db)
    if ultimo is None or ultimo.action != ACAO_LOGIN_RECUSADO:
        return
    _auditar_login(db, action=ACAO_LOGIN_OK, actor=ACTOR_WORKER, impressao=impressao)
    await db.commit()


async def liberar_login(db: AsyncSession, *, actor: str) -> bool:
    """Admin autoriza UMA nova tentativa. False se nada estava travado."""
    ultimo = await _ultimo_evento_login(db)
    if ultimo is None or ultimo.action != ACAO_LOGIN_RECUSADO:
        return False
    _auditar_login(
        db,
        action=ACAO_LOGIN_LIBERADO,
        actor=actor,
        impressao=ultimo.resource_id,
    )
    await db.commit()
    return True


async def estado_login(db: AsyncSession, impressao: str | None) -> dict[str, Any]:
    """O que a tela mostra sobre a trava."""
    bloqueio = await login_bloqueado(db, impressao)
    if bloqueio is None:
        return {"bloqueado": False, "desde": None, "mensagem": None}
    return {
        "bloqueado": True,
        "desde": bloqueio.created_at.isoformat() if bloqueio.created_at else None,
        "mensagem": MSG_LOGIN_BLOQUEADO,
    }


async def executar_carga(
    db: AsyncSession, client: ClienteEasyjur, *, source: str, impressao: str | None
) -> ResultadoSync:
    """O pull com a trava de login. E o que o worker chama.

    Credencial ja recusada: levanta `LoginEasyjurBloqueado` SEM tocar no
    EasyJur. Recusa nova: grava a trava e levanta. Login aceito: fecha
    qualquer trava anterior, mesmo que a carga falhe depois.
    """
    if await login_bloqueado(db, impressao) is not None:
        logger.error("easyjur.login_bloqueado source=%s -- nao tentou", source)
        raise LoginEasyjurBloqueado(MSG_LOGIN_BLOQUEADO)

    await marcar_inicio(db, source=source)
    try:
        r = await sincronizar(db, client, source=source)
    except EasyjurLoginRecusadoError as exc:
        await db.rollback()
        if impressao is not None:
            await registrar_login_recusado(db, impressao, exc)
        raise LoginEasyjurBloqueado(f"{MSG_LOGIN_BLOQUEADO} Resposta: {exc}") from exc
    except Exception:
        if impressao is not None and getattr(client, "login_confirmado", False):
            await db.rollback()
            await registrar_login_ok(db, impressao)
        raise
    if impressao is not None and getattr(client, "login_confirmado", False):
        await registrar_login_ok(db, impressao)
    return r


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


async def resumo(db: AsyncSession, *, impressao: str | None = None) -> dict[str, Any]:
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
        "login_easyjur": await estado_login(db, impressao),
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
