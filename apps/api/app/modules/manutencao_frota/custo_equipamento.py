"""Custo e apropriacao por equipamento -- roadmap B#7.

**O que estava faltando.** `calcular_consumo_parte_diaria` ja resolve
UM apontamento: horas, km, litros por hora, custo por hora. Mas custo
por equipamento e uma pergunta de periodo, nao de dia: "quanto esta
maquina consumiu no mes, e isso esta fora da curva?". Nenhuma tela
respondia isso, e a apropriacao vivia na planilha do Bruno.

**A referencia e a propria frota.** Nao ha tabela de consumo nominal
por modelo, e inventar um numero de catalogo seria pior do que nao
alertar: a escavadeira em terreno pesado consome legitimamente mais que
a de patio. Entao a referencia e a **mediana dos pares** -- equipamentos
do mesmo `tipo` no mesmo periodo. Um desvio acima do limite diz "este
esta fora da curva DA SUA FROTA", que e uma afirmacao defensavel.

Mediana, nao media: um unico equipamento com vazamento puxa a media e
esconde os outros. A mediana aguenta o outlier que estamos justamente
tentando achar.

**O que este motor NAO faz.** Nao cruza com a NF fiscal do posto. O
`combustivel_custo` e o valor que o apontador digita em campo (PWA), e
comparar o apontado com o faturado e o passo seguinte -- depende das
notas de combustivel, que hoje estao na planilha do SharePoint. A
divergencia apontado × faturado e exatamente o tipo de coisa que este
motor vai achar quando a nota chegar; por ora ele compara consumo entre
pares, que ja e acionavel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.manutencao_frota.models import (
    PARTE_PROCESSADO,
    PARTE_REVISADO,
    ParteDiaria,
    Veiculo,
)

# Apontamento pendente ou com erro de OCR nao entra no custo: o numero
# ainda pode mudar na revisao, e custo que muda sozinho depois de
# apresentado destroi a confianca no painel.
STATUSES_CONFIAVEIS = (PARTE_REVISADO, PARTE_PROCESSADO)

# Acima de quanto da mediana dos pares um equipamento vira alerta.
# 1.30 = 30% acima. Numero escolhido para pegar vazamento/bomba
# viciada/desvio sem disparar na variacao normal de terreno e operador;
# e ajustavel quando o Bruno olhar a primeira safra de alertas.
LIMITE_DESVIO = Decimal("1.30")

# Abaixo disso a mediana nao significa nada -- com dois equipamentos,
# "a mediana dos pares" e so o outro equipamento.
MINIMO_PARES_PARA_REFERENCIA = 3


@dataclass
class CustoEquipamento:
    """Apropriacao de um equipamento no periodo.

    Horas e km convivem porque a frota e mista: maquina tem horimetro,
    caminhao tem odometro, e alguns registram os dois. Cada metrica e
    independente -- faltar litro nao invalida as horas trabalhadas.
    """

    veiculo_id: int
    placa: str
    tipo: str | None
    modelo: str | None
    obra: str | None
    apontamentos: int
    horas_trabalhadas: Decimal
    km_rodados: int
    litros: Decimal
    custo_total: Decimal
    consumo_litros_por_hora: Decimal | None
    consumo_km_por_litro: Decimal | None
    custo_por_hora: Decimal | None
    custo_por_km: Decimal | None
    # Preenchidos na comparacao com os pares.
    referencia_litros_por_hora: Decimal | None = None
    desvio_percentual: Decimal | None = None
    alerta_custo: bool = False

    def como_dict(self) -> dict[str, Any]:
        return {
            "veiculo_id": self.veiculo_id,
            "placa": self.placa,
            "tipo": self.tipo,
            "modelo": self.modelo,
            "obra": self.obra,
            "apontamentos": self.apontamentos,
            "horas_trabalhadas": self.horas_trabalhadas,
            "km_rodados": self.km_rodados,
            "litros": self.litros,
            "custo_total": self.custo_total,
            "consumo_litros_por_hora": self.consumo_litros_por_hora,
            "consumo_km_por_litro": self.consumo_km_por_litro,
            "custo_por_hora": self.custo_por_hora,
            "custo_por_km": self.custo_por_km,
            "referencia_litros_por_hora": self.referencia_litros_por_hora,
            "desvio_percentual": self.desvio_percentual,
            "alerta_custo": self.alerta_custo,
        }


def _mediana(valores: list[Decimal]) -> Decimal | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    if len(ordenados) % 2 == 1:
        return ordenados[meio]
    return (ordenados[meio - 1] + ordenados[meio]) / 2


def _agregar(veiculo: Veiculo, partes: list[ParteDiaria]) -> CustoEquipamento:
    """Soma o periodo inteiro antes de dividir.

    Somar as horas e os litros e dividir uma vez no fim NAO da o mesmo
    que a media dos consumos diarios: um dia de 30 minutos pesaria
    igual a um dia de 10 horas. O consumo do periodo e a razao dos
    totais.
    """
    horas = Decimal("0")
    km = 0
    litros = Decimal("0")
    custo = Decimal("0")

    for p in partes:
        if p.horimetro_inicio is not None and p.horimetro_fim is not None:
            delta = p.horimetro_fim - p.horimetro_inicio
            if delta > 0:
                horas += delta
        if p.km_inicio is not None and p.km_fim is not None:
            delta_km = p.km_fim - p.km_inicio
            if delta_km > 0:
                km += delta_km
        if p.combustivel_litros is not None and p.combustivel_litros > 0:
            litros += p.combustivel_litros
        if p.combustivel_custo is not None and p.combustivel_custo > 0:
            custo += p.combustivel_custo

    cons_lh = (
        (litros / horas).quantize(Decimal("0.001"))
        if horas > 0 and litros > 0
        else None
    )
    cons_kml = (
        (Decimal(km) / litros).quantize(Decimal("0.001"))
        if km > 0 and litros > 0
        else None
    )
    custo_h = (
        (custo / horas).quantize(Decimal("0.01")) if horas > 0 and custo > 0 else None
    )
    custo_km = (
        (custo / Decimal(km)).quantize(Decimal("0.01"))
        if km > 0 and custo > 0
        else None
    )

    return CustoEquipamento(
        veiculo_id=veiculo.id,
        placa=veiculo.placa,
        tipo=veiculo.tipo,
        modelo=veiculo.modelo,
        obra=veiculo.obra,
        apontamentos=len(partes),
        horas_trabalhadas=horas,
        km_rodados=km,
        litros=litros,
        custo_total=custo,
        consumo_litros_por_hora=cons_lh,
        consumo_km_por_litro=cons_kml,
        custo_por_hora=custo_h,
        custo_por_km=custo_km,
    )


def _marcar_fora_da_curva(itens: list[CustoEquipamento]) -> None:
    """Compara cada equipamento com a mediana dos pares do mesmo tipo.

    Equipamento sem tipo definido nao e comparado -- agrupar "sem tipo"
    misturaria escavadeira com caminhonete e produziria alerta que a
    pessoa aprende a ignorar. Silencio e melhor que alerta falso.
    """
    por_tipo: dict[str, list[CustoEquipamento]] = {}
    for item in itens:
        if item.tipo and item.consumo_litros_por_hora is not None:
            por_tipo.setdefault(item.tipo, []).append(item)

    for grupo in por_tipo.values():
        if len(grupo) < MINIMO_PARES_PARA_REFERENCIA:
            continue
        referencia = _mediana(
            [i.consumo_litros_por_hora for i in grupo]  # type: ignore[misc]
        )
        if referencia is None or referencia <= 0:
            continue
        for item in grupo:
            consumo = item.consumo_litros_por_hora
            assert consumo is not None  # filtrado acima
            item.referencia_litros_por_hora = referencia
            item.desvio_percentual = (
                (consumo / referencia - 1) * 100
            ).quantize(Decimal("0.1"))
            item.alerta_custo = consumo > referencia * LIMITE_DESVIO


async def apropriacao_por_equipamento(
    db: AsyncSession,
    *,
    inicio: date | None = None,
    fim: date | None = None,
    obra: str | None = None,
) -> dict[str, Any]:
    """Custo e consumo de cada equipamento no periodo, com fora-da-curva.

    Devolve tambem os totais da frota: a pergunta "quanto a frota
    custou neste mes" nao deve exigir somar a tabela na mao.
    """
    q = select(ParteDiaria).where(
        ParteDiaria.veiculo_id.isnot(None),
        ParteDiaria.ocr_status.in_(STATUSES_CONFIAVEIS),
    )
    if inicio is not None:
        q = q.where(ParteDiaria.data >= inicio)
    if fim is not None:
        q = q.where(ParteDiaria.data <= fim)
    if obra:
        q = q.where(ParteDiaria.obra == obra)

    partes = list((await db.execute(q)).scalars().all())

    por_veiculo: dict[int, list[ParteDiaria]] = {}
    for p in partes:
        if p.veiculo_id is not None:
            por_veiculo.setdefault(p.veiculo_id, []).append(p)

    itens: list[CustoEquipamento] = []
    for veiculo_id, lote in por_veiculo.items():
        veiculo = await db.get(Veiculo, veiculo_id)
        if veiculo is None:
            continue
        itens.append(_agregar(veiculo, lote))

    _marcar_fora_da_curva(itens)
    # Maior custo primeiro: quem abre a tela quer ver onde o dinheiro
    # esta indo, nao a ordem de cadastro.
    itens.sort(key=lambda i: i.custo_total, reverse=True)

    return {
        "periodo": {
            "inicio": inicio.isoformat() if inicio else None,
            "fim": fim.isoformat() if fim else None,
        },
        "obra": obra,
        "equipamentos": [i.como_dict() for i in itens],
        "totais": {
            "equipamentos": len(itens),
            "apontamentos": sum(i.apontamentos for i in itens),
            "horas": sum((i.horas_trabalhadas for i in itens), Decimal("0")),
            "km": sum(i.km_rodados for i in itens),
            "litros": sum((i.litros for i in itens), Decimal("0")),
            "custo_total": sum((i.custo_total for i in itens), Decimal("0")),
        },
        "fora_da_curva": [i.como_dict() for i in itens if i.alerta_custo],
    }


__all__ = [
    "LIMITE_DESVIO",
    "MINIMO_PARES_PARA_REFERENCIA",
    "CustoEquipamento",
    "apropriacao_por_equipamento",
]
