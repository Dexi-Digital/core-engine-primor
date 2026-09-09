"""Ingestao do ponto eletronico (Solides/Tangerino).

Somente leitura. O adapter nao tem metodo de escrita e este servico
nunca cria funcionario no DP -- `dp_employees` continua sendo a fonte
da verdade do cadastro; aqui so ligamos por CPF quando ja existe.

Roda no worker sob lock single-flight (`app.core.locks`). Diferente do
TOTVS, o Solides nao cobra licenca por requisicao, mas a paginacao de
395 funcionarios x N batidas e pesada o suficiente para nao querer duas
execucoes sobrepostas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt.models import Employee
from app.modules.obras.models import Obra
from app.modules.ponto.matching import codigo_obra_de_local, normalizar_nome_local
from app.modules.ponto.models import (
    PontoBatida,
    PontoFuncionario,
    PontoLocalTrabalho,
    PontoSyncLog,
)

logger = logging.getLogger(__name__)

# A API do Solides NAO PAGINA. Confirmado contra a conta real em
# 28/08/2026: `page`, `offset`, `start` e `pageNumber` devolvem sempre
# os mesmos registros; apenas `size` e honrado, e um `size` grande
# devolve a colecao inteira (396 de 396 funcionarios). Por isso nao ha
# laco de paginacao aqui -- ha UMA requisicao pedindo tudo, e uma
# conferencia contra `total` para o dia em que a colecao passar do teto.
# 2000 era baixo: a conta da Primor tem 2400 funcionarios contando os
# demitidos, e a API devolve os demitidos PRIMEIRO -- o truncamento
# cortava exatamente os ~400 ativos, que sao os que interessam para
# batidas. A guarda de truncamento acima foi o que revelou isso.
LIMITE_UNICA_REQUISICAO = 5000


@dataclass(frozen=True)
class IngestResumo:
    recurso: str
    janela: str
    source: str
    lidos: int
    gravados: int
    sem_vinculo: int = 0


def _dt(epoch_ms: Any) -> datetime | None:
    """Epoch em MILISSEGUNDOS -> datetime UTC.

    Confirmado contra a API real em 27/08/2026: `birthDate` e
    `admissionDate` vem em ms (`1787022000000`). Tratar como segundos
    jogaria as datas para 1970.
    """
    if epoch_ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        logger.warning("ponto: timestamp invalido ignorado: %r", epoch_ms)
        return None


def _data(epoch_ms: Any) -> date | None:
    momento = _dt(epoch_ms)
    return momento.date() if momento else None


async def _buscar_tudo(recurso: str, chamar) -> list[dict[str, Any]]:
    """Uma unica requisicao pedindo a colecao inteira.

    Nao ha laco: a API ignora qualquer parametro de avanco de pagina
    (ver constante acima). Se `total` vier maior que o recebido, a
    leitura esta TRUNCADA -- registramos alto, porque calar aqui viraria
    dado faltando sem sintoma.
    """
    envelope = await chamar(LIMITE_UNICA_REQUISICAO)
    itens = envelope.get("items") or []
    total = envelope.get("total")
    if isinstance(total, int) and total > len(itens):
        logger.warning(
            "ponto: leitura de %s TRUNCADA -- a API declara %d registros e "
            "devolveu %d. Aumentar LIMITE_UNICA_REQUISICAO ou rever se a "
            "API passou a paginar.",
            recurso, total, len(itens),
        )
    return itens


async def _log(
    db: AsyncSession,
    *,
    source: str,
    janela: str,
    recurso: str,
    status: str,
    lidos: int = 0,
    gravados: int = 0,
    error_message: str | None = None,
) -> None:
    log = await db.scalar(
        select(PontoSyncLog).where(
            PontoSyncLog.source == source,
            PontoSyncLog.janela == janela,
            PontoSyncLog.recurso == recurso,
        )
    )
    if log is None:
        log = PontoSyncLog(source=source, janela=janela, recurso=recurso)
        db.add(log)
    log.status = status
    log.lidos = lidos
    log.gravados = gravados
    log.error_message = error_message[:2048] if error_message else None
    await db.commit()


# ------------------------------------------------------------- locais
async def ingest_locais_trabalho(
    db: AsyncSession, client: Any, *, source: str = "beat"
) -> IngestResumo:
    """Puxa os locais de trabalho e tenta ligar cada um a uma obra.

    O vinculo e por `codigo_obra` extraido do nome ("Obra 243" -> "243")
    contra `obras_obra.codigo`. Local administrativo fica sem codigo e
    sem obra -- correto. Local de obra ainda nao cadastrada fica com
    codigo e sem `obra_id`, visivel como pendencia.
    """
    janela = date.today().isoformat()
    try:
        itens = await _buscar_tudo(
            "locais", lambda n: client.list_locais_trabalho(page=0, size=n)
        )
    except Exception as exc:  # noqa: BLE001 -- relogado e re-levantado
        await _log(
            db, source=source, janela=janela, recurso="locais",
            status="failed", error_message=str(exc),
        )
        logger.exception("ponto: pull de locais falhou")
        raise

    # Um SELECT so para todas as obras -- evita N+1 com 83 locais.
    obras = {
        (o.codigo or "").upper(): o.id
        for o in (await db.scalars(select(Obra))).all()
    }

    gravados = 0
    sem_vinculo = 0
    for item in itens:
        tid = item.get("id")
        if tid is None:
            continue
        nome = item.get("nome") or ""
        codigo = codigo_obra_de_local(nome)
        obra_id = obras.get(codigo) if codigo else None
        if codigo and obra_id is None:
            sem_vinculo += 1

        linha = await db.scalar(
            select(PontoLocalTrabalho).where(
                PontoLocalTrabalho.tangerino_id == int(tid)
            )
        )
        if linha is None:
            linha = PontoLocalTrabalho(tangerino_id=int(tid))
            db.add(linha)
        linha.nome = nome
        linha.nome_normalizado = normalizar_nome_local(nome)
        linha.ativo = bool(item.get("ativo", True))
        linha.codigo_obra = codigo
        linha.obra_id = obra_id
        gravados += 1

    await db.commit()
    await _log(
        db, source=source, janela=janela, recurso="locais",
        status="ok", lidos=len(itens), gravados=gravados,
    )
    return IngestResumo(
        recurso="locais", janela=janela, source=source,
        lidos=len(itens), gravados=gravados, sem_vinculo=sem_vinculo,
    )


# -------------------------------------------------------- funcionarios
async def ingest_funcionarios(
    db: AsyncSession,
    client: Any,
    *,
    source: str = "beat",
    incluir_demitidos: bool = False,
) -> IngestResumo:
    """Puxa os funcionarios do Solides e liga por CPF ao `dp_employees`.

    NUNCA cria funcionario no DP. CPF que nao existe la fica com
    `employee_id` nulo -- a tela lista como pendencia de cadastro, que e
    trabalho humano, nao adivinhacao.

    LIMITACAO DO FORNECEDOR (medida em 28/08/2026): a API nao pagina e
    limita `size` a 2000 no servidor. A conta da Primor tem 2400
    funcionarios contando demitidos, e a API devolve os DEMITIDOS
    primeiro -- entao pedir tudo traz 2000 demitidos e ZERO ativos.
    Por isso o default e `incluir_demitidos=False`, que traz os ~396
    ativos, que sao os que geram batida e apropriacao por obra.

    Consequencia aceita: o historico completo de demitidos nao e
    recuperavel em lote enquanto a Solides nao expuser paginacao. Se um
    demitido recente precisar entrar, hoje seria por consulta pontual.
    """
    janela = date.today().isoformat()
    try:
        itens = await _buscar_tudo(
            "funcionarios",
            lambda n: client.list_funcionarios(
                page=0, size=n, incluir_demitidos=incluir_demitidos
            ),
        )
    except Exception as exc:  # noqa: BLE001
        await _log(
            db, source=source, janela=janela, recurso="funcionarios",
            status="failed", error_message=str(exc),
        )
        logger.exception("ponto: pull de funcionarios falhou")
        raise

    por_cpf = {
        e.cpf: e.id
        for e in (await db.scalars(select(Employee))).all()
        if e.cpf
    }

    gravados = 0
    sem_vinculo = 0
    for item in itens:
        tid = item.get("id")
        if tid is None:
            continue
        cpf = item.get("cpf")
        employee_id = por_cpf.get(cpf) if cpf else None
        if employee_id is None:
            sem_vinculo += 1

        workplaces = item.get("workplaces") or []
        wp = workplaces[0] if workplaces else {}

        linha = await db.scalar(
            select(PontoFuncionario).where(
                PontoFuncionario.tangerino_id == int(tid)
            )
        )
        if linha is None:
            linha = PontoFuncionario(tangerino_id=int(tid))
            db.add(linha)
        linha.nome = item.get("nome")
        linha.cpf = cpf
        linha.pis = item.get("pis")
        linha.data_admissao = _data(item.get("admissao"))
        linha.demitido = bool(item.get("demitido"))
        linha.employee_id = employee_id
        linha.local_trabalho_externo_id = wp.get("id")
        linha.local_trabalho_nome = wp.get("nome")
        gravados += 1

    await db.commit()
    await _log(
        db, source=source, janela=janela, recurso="funcionarios",
        status="ok", lidos=len(itens), gravados=gravados,
    )
    return IngestResumo(
        recurso="funcionarios", janela=janela, source=source,
        lidos=len(itens), gravados=gravados, sem_vinculo=sem_vinculo,
    )


# ------------------------------------------------------------ batidas
def external_id_batida(tangerino_employee_id: int, item: dict[str, Any]) -> str:
    """Chave natural da batida.

    O Solides nao expoe id proprio da batida, entao a chave e
    funcionario + dia + inicio. Mesmo papel do `external_id` do PNCP e
    do TOTVS: permite reprocessar a mesma janela sem duplicar.
    """
    dia = _data(item.get("data_trabalho_ts"))
    inicio = item.get("inicio_ts") or "0"
    return f"{tangerino_employee_id}-{dia or 'sem-data'}-{inicio}"


async def ingest_batidas(
    db: AsyncSession,
    client: Any,
    *,
    desde: date,
    ate: date,
    source: str = "beat",
) -> IngestResumo:
    """Puxa as batidas do periodo, um funcionario por vez.

    A API do Solides so devolve batidas POR funcionario -- nao ha
    endpoint de "todas as batidas do periodo". Por isso o loop, e por
    isso o pull e noturno.
    """
    janela = f"{desde.isoformat()}..{ate.isoformat()}"
    funcionarios = (
        await db.scalars(
            select(PontoFuncionario).where(PontoFuncionario.demitido.is_(False))
        )
    ).all()

    lidos = 0
    gravados = 0
    try:
        for func in funcionarios:
            itens = await _buscar_tudo(
                f"batidas:{func.tangerino_id}",
                lambda n, f=func: client.list_batidas(
                    f.tangerino_id,
                    start_date=desde.isoformat(),
                    end_date=ate.isoformat(),
                    page=0,
                    size=n,
                ),
            )
            lidos += len(itens)
            for item in itens:
                ext = external_id_batida(func.tangerino_id, item)
                linha = await db.scalar(
                    select(PontoBatida).where(PontoBatida.external_id == ext)
                )
                if linha is None:
                    linha = PontoBatida(external_id=ext)
                    db.add(linha)
                linha.tangerino_employee_id = func.tangerino_id
                linha.funcionario_id = func.id
                linha.data_trabalho = _data(item.get("data_trabalho_ts"))
                linha.inicio = _dt(item.get("inicio_ts"))
                linha.fim = _dt(item.get("fim_ts"))
                linha.segundos_trabalhados = item.get("segundos_trabalhados")
                linha.status = item.get("status")
                gravados += 1
    except Exception as exc:  # noqa: BLE001
        await _log(
            db, source=source, janela=janela, recurso="batidas",
            status="failed", error_message=str(exc),
        )
        logger.exception("ponto: pull de batidas falhou")
        raise

    await db.commit()
    await _log(
        db, source=source, janela=janela, recurso="batidas",
        status="ok", lidos=lidos, gravados=gravados,
    )
    return IngestResumo(
        recurso="batidas", janela=janela, source=source,
        lidos=lidos, gravados=gravados,
    )
