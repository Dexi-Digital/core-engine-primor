"""HTTP router do ponto eletronico (Solides/Tangerino).

Somente leitura. O pull acontece no worker; aqui a API so serve o que
ja foi ingerido, mais os dois painéis de pendencia que existem para ser
resolvidos por gente:

  - local de trabalho que aponta para uma obra ainda nao cadastrada
  - funcionario do ponto cujo CPF nao existe no cadastro do DP
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.obras.models import Obra
from app.modules.ponto.models import (
    PontoBatida,
    PontoFuncionario,
    PontoLocalTrabalho,
    PontoSyncLog,
)

router = APIRouter(prefix="/api/v1/ponto", tags=["ponto"])


@router.get("/resumo")
async def resumo_endpoint(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Numeros de topo + a ultima execucao de cada recurso."""
    total_locais = await db.scalar(select(func.count()).select_from(PontoLocalTrabalho))
    com_obra = await db.scalar(
        select(func.count())
        .select_from(PontoLocalTrabalho)
        .where(PontoLocalTrabalho.obra_id.is_not(None))
    )
    obra_pendente = await db.scalar(
        select(func.count())
        .select_from(PontoLocalTrabalho)
        .where(
            PontoLocalTrabalho.codigo_obra.is_not(None),
            PontoLocalTrabalho.obra_id.is_(None),
        )
    )
    administrativos = await db.scalar(
        select(func.count())
        .select_from(PontoLocalTrabalho)
        .where(PontoLocalTrabalho.codigo_obra.is_(None))
    )
    total_func = await db.scalar(
        select(func.count())
        .select_from(PontoFuncionario)
        .where(PontoFuncionario.demitido.is_(False))
    )
    func_sem_dp = await db.scalar(
        select(func.count())
        .select_from(PontoFuncionario)
        .where(
            PontoFuncionario.demitido.is_(False),
            PontoFuncionario.employee_id.is_(None),
        )
    )
    total_batidas = await db.scalar(select(func.count()).select_from(PontoBatida))

    execucoes = (
        await db.scalars(
            select(PontoSyncLog).order_by(PontoSyncLog.started_at.desc()).limit(6)
        )
    ).all()

    return {
        "locais": {
            "total": total_locais or 0,
            "com_obra": com_obra or 0,
            "obra_nao_cadastrada": obra_pendente or 0,
            "administrativos": administrativos or 0,
        },
        "funcionarios": {
            "ativos": total_func or 0,
            "sem_cadastro_dp": func_sem_dp or 0,
        },
        "batidas": {"total": total_batidas or 0},
        "execucoes": [
            {
                "recurso": e.recurso,
                "source": e.source,
                "janela": e.janela,
                "status": e.status,
                "lidos": e.lidos,
                "gravados": e.gravados,
                "error_message": e.error_message,
                "started_at": e.started_at.isoformat() if e.started_at else None,
            }
            for e in execucoes
        ],
    }


@router.get("/locais")
async def listar_locais_endpoint(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    apenas_pendentes: bool = False,
) -> list[dict[str, Any]]:
    """Locais de trabalho e a obra ligada a cada um.

    `apenas_pendentes=true` filtra os que tem codigo de obra mas nao
    acharam a obra no cadastro -- a fila de trabalho humano.
    """
    stmt = (
        select(PontoLocalTrabalho, Obra)
        .outerjoin(Obra, PontoLocalTrabalho.obra_id == Obra.id)
        .order_by(PontoLocalTrabalho.nome)
    )
    if apenas_pendentes:
        stmt = stmt.where(
            PontoLocalTrabalho.codigo_obra.is_not(None),
            PontoLocalTrabalho.obra_id.is_(None),
        )
    linhas = (await db.execute(stmt)).all()
    return [
        {
            "id": local.id,
            "tangerino_id": local.tangerino_id,
            "nome": local.nome,
            "ativo": local.ativo,
            "codigo_obra": local.codigo_obra,
            "obra_id": local.obra_id,
            "obra_nome": obra.nome if obra else None,
            "vinculo": (
                "ligado" if local.obra_id
                else "obra_nao_cadastrada" if local.codigo_obra
                else "administrativo"
            ),
        }
        for local, obra in linhas
    ]


@router.get("/funcionarios")
async def listar_funcionarios_endpoint(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    apenas_sem_cadastro: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Funcionarios do ponto. NAO devolve CPF: a tela nao precisa dele
    para resolver a pendencia, e minimizar dado pessoal e a regra do
    repo (LGPD). O vinculo se resolve pelo nome + local."""
    stmt = (
        select(PontoFuncionario)
        .where(PontoFuncionario.demitido.is_(False))
        .order_by(PontoFuncionario.nome)
        .limit(min(limit, 500))
    )
    if apenas_sem_cadastro:
        stmt = stmt.where(PontoFuncionario.employee_id.is_(None))
    linhas = (await db.scalars(stmt)).all()
    return [
        {
            "id": f.id,
            "tangerino_id": f.tangerino_id,
            "nome": f.nome,
            "local_trabalho_nome": f.local_trabalho_nome,
            "data_admissao": f.data_admissao.isoformat() if f.data_admissao else None,
            "employee_id": f.employee_id,
            "no_cadastro_dp": f.employee_id is not None,
        }
        for f in linhas
    ]
