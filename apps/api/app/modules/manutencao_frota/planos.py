"""Planos de manutencao -- revisoes programadas por equipamento.

**O que faltava.** O gatilho global do `service.py` responde "cruzou
3.000 km / 50 h desde o ultimo apontamento?" para a frota inteira. E a
regra do Bruno, e um bom default, mas nao e um plano: o mesmo
equipamento tem troca de oleo num intervalo, filtro noutro, correia
noutro. Isso vivia so na planilha do SharePoint (documentos -> controle
de manutencao -> controle de revisoes).

**Uso, nao calendario.** O vencimento e medido em horimetro/odometro, e
nao em data. Maquina parada tres meses nao precisa de troca de oleo por
causa do tempo -- e alerta que dispara por calendario numa frota que
fica ociosa na entressafra ensina a equipe a ignorar o alerta.

**A leitura atual vem do que a frota ja registra:** o maior
`horimetro_fim` / `km_fim` das partes diarias confiaveis, com o
`km_atual` do cadastro como piso (ele e atualizado na mao e pode estar
a frente do ultimo apontamento). Sem leitura nenhuma, o plano fica
`sem_leitura` em vez de "ok" -- dizer "em dia" sobre equipamento que
nunca apontou seria afirmar o que nao se sabe.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.modules.manutencao_frota.models import (
    PARTE_PROCESSADO,
    PARTE_REVISADO,
    PLANO_BASE_HORAS,
    PLANO_BASE_KM,
    PLANO_OK,
    PLANO_PROXIMA,
    PLANO_SEM_LEITURA,
    PLANO_VENCIDA,
    ParteDiaria,
    PlanoManutencao,
    Veiculo,
)

STATUSES_CONFIAVEIS = (PARTE_REVISADO, PARTE_PROCESSADO)

# A partir de quanto do intervalo restante o plano entra em "proxima".
# Fracao e nao valor fixo: 10% de 3.000 km sao 300 km de aviso, e 10%
# de 50 h sao 5 h -- os dois dao tempo de programar a parada, o que um
# numero unico nao conseguiria para as duas bases.
FRACAO_AVISO = Decimal("0.10")


@dataclass
class StatusPlano:
    plano_id: int
    veiculo_id: int
    placa: str
    descricao: str
    base: str
    intervalo: Decimal
    leitura_atual: Decimal | None
    ultima_revisao_marcador: Decimal | None
    proxima_em: Decimal | None
    falta: Decimal | None
    status: str

    def como_dict(self) -> dict[str, Any]:
        return {
            "plano_id": self.plano_id,
            "veiculo_id": self.veiculo_id,
            "placa": self.placa,
            "descricao": self.descricao,
            "base": self.base,
            "intervalo": self.intervalo,
            "leitura_atual": self.leitura_atual,
            "ultima_revisao_marcador": self.ultima_revisao_marcador,
            "proxima_em": self.proxima_em,
            "falta": self.falta,
            "status": self.status,
        }


async def leitura_atual(
    db: AsyncSession, veiculo: Veiculo, base: str
) -> Decimal | None:
    """Horimetro/odometro corrente do equipamento.

    Pega o MAIOR valor entre o cadastro e os apontamentos: o
    `km_atual` e digitado a mao e as vezes esta mais novo que a ultima
    parte diaria processada. Escolher o menor faria o sistema achar que
    falta mais rodagem do que falta -- e adiar revisao e o erro caro.
    """
    campo = (
        ParteDiaria.horimetro_fim
        if base == PLANO_BASE_HORAS
        else ParteDiaria.km_fim
    )
    res = await db.execute(
        select(func.max(campo))
        .where(ParteDiaria.veiculo_id == veiculo.id)
        .where(ParteDiaria.ocr_status.in_(STATUSES_CONFIAVEIS))
    )
    das_partes = res.scalar_one_or_none()

    candidatos: list[Decimal] = []
    if das_partes is not None:
        candidatos.append(Decimal(str(das_partes)))
    if base == PLANO_BASE_KM and veiculo.km_atual is not None:
        candidatos.append(Decimal(veiculo.km_atual))
    return max(candidatos) if candidatos else None


def avaliar(
    plano: PlanoManutencao, placa: str, leitura: Decimal | None
) -> StatusPlano:
    """Estado de um plano, dada a leitura corrente.

    Funcao pura -- e o que permite testar a regra sem banco.

    Sem leitura OU sem marcador da ultima revisao, o status e
    `sem_leitura`: nao da para dizer que falta X quando nao se sabe nem
    de onde contar.
    """
    base_ok = plano.ultima_revisao_marcador
    if leitura is None or base_ok is None:
        return StatusPlano(
            plano_id=plano.id,
            veiculo_id=plano.veiculo_id,
            placa=placa,
            descricao=plano.descricao,
            base=plano.base,
            intervalo=plano.intervalo,
            leitura_atual=leitura,
            ultima_revisao_marcador=base_ok,
            proxima_em=None,
            falta=None,
            status=PLANO_SEM_LEITURA,
        )

    proxima = base_ok + plano.intervalo
    falta = proxima - leitura
    if falta <= 0:
        status = PLANO_VENCIDA
    elif falta <= plano.intervalo * FRACAO_AVISO:
        status = PLANO_PROXIMA
    else:
        status = PLANO_OK

    return StatusPlano(
        plano_id=plano.id,
        veiculo_id=plano.veiculo_id,
        placa=placa,
        descricao=plano.descricao,
        base=plano.base,
        intervalo=plano.intervalo,
        leitura_atual=leitura,
        ultima_revisao_marcador=base_ok,
        proxima_em=proxima,
        falta=falta,
        status=status,
    )


# Vencida antes de proxima, e sem_leitura antes de ok: a ordem da lista
# e a ordem em que a pessoa deve agir.
_ORDEM = {
    PLANO_VENCIDA: 0,
    PLANO_PROXIMA: 1,
    PLANO_SEM_LEITURA: 2,
    PLANO_OK: 3,
}


async def status_dos_planos(
    db: AsyncSession, *, veiculo_id: int | None = None
) -> dict[str, Any]:
    """Estado de todos os planos ativos, o que vence primeiro no topo."""
    q = select(PlanoManutencao).where(PlanoManutencao.ativo.is_(True))
    if veiculo_id is not None:
        q = q.where(PlanoManutencao.veiculo_id == veiculo_id)
    planos = list((await db.execute(q.order_by(PlanoManutencao.id))).scalars().all())

    # Cache por (veiculo, base): varios planos do mesmo equipamento na
    # mesma base compartilham a leitura, e sem isso seria uma query por
    # plano.
    cache: dict[tuple[int, str], Decimal | None] = {}
    itens: list[StatusPlano] = []

    for plano in planos:
        veiculo = await db.get(Veiculo, plano.veiculo_id)
        if veiculo is None:
            continue
        chave = (plano.veiculo_id, plano.base)
        if chave not in cache:
            cache[chave] = await leitura_atual(db, veiculo, plano.base)
        itens.append(avaliar(plano, veiculo.placa, cache[chave]))

    itens.sort(key=lambda i: (_ORDEM.get(i.status, 9), i.falta or Decimal(0)))

    por_status: dict[str, int] = {
        PLANO_VENCIDA: 0,
        PLANO_PROXIMA: 0,
        PLANO_OK: 0,
        PLANO_SEM_LEITURA: 0,
    }
    for i in itens:
        por_status[i.status] = por_status.get(i.status, 0) + 1

    return {
        "total": len(itens),
        "por_status": por_status,
        "vencidas": [i.como_dict() for i in itens if i.status == PLANO_VENCIDA],
        "planos": [i.como_dict() for i in itens],
    }


async def veiculos_sem_plano(db: AsyncSession) -> list[dict[str, Any]]:
    """Equipamentos ativos sem nenhum plano cadastrado.

    Existe para a ausencia aparecer. Sem esta lista, um equipamento sem
    plano fica invisivel no painel -- e "nao apareceu nenhum alerta"
    seria lido como "esta tudo em dia", que e o pior modo de falha de um
    controle de manutencao. Para esses, o gatilho global do Bruno
    (3.000 km / 50 h) segue valendo por apontamento.
    """
    com_plano = select(PlanoManutencao.veiculo_id).where(
        PlanoManutencao.ativo.is_(True)
    )
    res = await db.execute(
        select(Veiculo)
        .where(Veiculo.status == "ativo")
        .where(Veiculo.id.notin_(com_plano))
        .order_by(Veiculo.placa)
    )
    return [
        {
            "veiculo_id": v.id,
            "placa": v.placa,
            "tipo": v.tipo,
            "modelo": v.modelo,
            "obra": v.obra,
        }
        for v in res.scalars().all()
    ]


__all__ = [
    "FRACAO_AVISO",
    "atualizar_plano",
    "criar_plano",
    "registrar_revisao",
    "StatusPlano",
    "avaliar",
    "leitura_atual",
    "status_dos_planos",
    "veiculos_sem_plano",
]


# --- CRUD -------------------------------------------------------------------
#
# Fica aqui e nao no `service.py` (1.200+ linhas) para o plano ter um
# arquivo que cabe na cabeca: regra, leitura e persistencia do mesmo
# assunto, juntos.


async def criar_plano(
    db: AsyncSession, dados: dict[str, Any], *, actor: str
) -> PlanoManutencao:
    plano = PlanoManutencao(**dados)
    db.add(plano)
    await db.commit()
    await db.refresh(plano)
    await _auditar(db, plano, "plano_criado", actor=actor)
    return plano


async def atualizar_plano(
    db: AsyncSession, plano: PlanoManutencao, campos: dict[str, Any], *, actor: str
) -> PlanoManutencao:
    for k, v in campos.items():
        setattr(plano, k, v)
    await db.commit()
    await db.refresh(plano)
    await _auditar(db, plano, "plano_atualizado", actor=actor)
    return plano


async def registrar_revisao(
    db: AsyncSession,
    plano: PlanoManutencao,
    *,
    marcador: Decimal | None,
    data_revisao: Any = None,
    observacoes: str | None = None,
    actor: str,
) -> PlanoManutencao:
    """Marca a revisao como feita e recomeca a contagem dali.

    Quando `marcador` nao vem, usa a leitura corrente do equipamento --
    quem registra no fim do dia raramente tem o horimetro anotado, e
    exigir o numero faria a revisao nao ser registrada, que e pior do
    que registra-la com a leitura do sistema.
    """
    if marcador is None:
        veiculo = await db.get(Veiculo, plano.veiculo_id)
        if veiculo is not None:
            marcador = await leitura_atual(db, veiculo, plano.base)
    if marcador is None:
        raise ValueError(
            "sem marcador informado e sem leitura do equipamento -- "
            "informe o horimetro/odometro da revisao"
        )

    plano.ultima_revisao_marcador = marcador
    if data_revisao is not None:
        plano.ultima_revisao_em = data_revisao
    if observacoes:
        plano.observacoes = observacoes
    await db.commit()
    await db.refresh(plano)
    await _auditar(db, plano, "revisao_registrada", actor=actor)
    return plano


async def _auditar(
    db: AsyncSession, plano: PlanoManutencao, acao: str, *, actor: str
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=acao,
            resource="manutencao_frota.plano",
            resource_id=str(plano.id),
            metadata_json=json.dumps(
                {
                    "veiculo_id": plano.veiculo_id,
                    "base": plano.base,
                    "intervalo": str(plano.intervalo),
                    "ultima_revisao_marcador": (
                        str(plano.ultima_revisao_marcador)
                        if plano.ultima_revisao_marcador is not None
                        else None
                    ),
                }
            ),
        )
    )
    await db.commit()
