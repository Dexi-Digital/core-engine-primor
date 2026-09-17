"""Custo por equipamento -- o que o motor RECUSA a afirmar.

A parte facil e somar litros. O que importa testar e a referencia:
quando ela nao existe, quando ela nao significa nada, e se a mediana
aguenta o outlier que estamos justamente tentando achar.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.manutencao_frota import custo_equipamento as svc
from app.modules.manutencao_frota.models import (
    PARTE_PENDENTE,
    PARTE_REVISADO,
    ParteDiaria,
    Veiculo,
)

pytestmark = pytest.mark.asyncio


async def _veiculo(
    db: AsyncSession, placa: str, *, tipo: str | None = "escavadeira", obra="Obra A"
) -> Veiculo:
    v = Veiculo(placa=placa, tipo=tipo, modelo="X", obra=obra)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _parte(
    db: AsyncSession,
    veiculo: Veiculo,
    *,
    h_ini="0",
    h_fim="10",
    litros="50",
    custo="300",
    status=PARTE_REVISADO,
    dia=date(2026, 9, 10),
    km_ini=None,
    km_fim=None,
) -> ParteDiaria:
    p = ParteDiaria(
        veiculo_id=veiculo.id,
        obra=veiculo.obra,
        data=dia,
        horimetro_inicio=Decimal(h_ini) if h_ini is not None else None,
        horimetro_fim=Decimal(h_fim) if h_fim is not None else None,
        km_inicio=km_ini,
        km_fim=km_fim,
        combustivel_litros=Decimal(litros) if litros is not None else None,
        combustivel_custo=Decimal(custo) if custo is not None else None,
        ocr_status=status,
    )
    db.add(p)
    await db.commit()
    return p


async def test_consumo_e_a_razao_dos_totais_nao_a_media_dos_dias(
    db_session: AsyncSession,
) -> None:
    """Somar antes de dividir.

    Um dia de 30 minutos nao pode pesar igual a um dia de 10 horas. Com
    media dos consumos diarios, a jornada curta distorce o mes inteiro.
    """
    v = await _veiculo(db_session, "AAA1A11")
    # 10h / 50L  (5 L/h)  +  1h / 20L  (20 L/h)
    await _parte(db_session, v, h_ini="0", h_fim="10", litros="50", custo="250")
    await _parte(db_session, v, h_ini="10", h_fim="11", litros="20", custo="100")

    res = await svc.apropriacao_por_equipamento(db_session)
    item = res["equipamentos"][0]

    # 70L / 11h = 6.363..., nao (5 + 20) / 2 = 12.5
    assert item["consumo_litros_por_hora"] == Decimal("6.364")
    assert item["horas_trabalhadas"] == Decimal("11")
    assert item["custo_total"] == Decimal("350")


async def test_apontamento_pendente_nao_entra_no_custo(
    db_session: AsyncSession,
) -> None:
    """Numero que muda sozinho depois de apresentado destroi o painel."""
    v = await _veiculo(db_session, "BBB2B22")
    await _parte(db_session, v, litros="50", custo="300")
    await _parte(db_session, v, litros="999", custo="9999", status=PARTE_PENDENTE)

    res = await svc.apropriacao_por_equipamento(db_session)
    assert res["equipamentos"][0]["litros"] == Decimal("50")
    assert res["totais"]["apontamentos"] == 1


async def test_sem_pares_suficientes_nao_ha_referencia(
    db_session: AsyncSession,
) -> None:
    """Com dois equipamentos, 'a mediana dos pares' e so o outro.

    Preferimos nao afirmar a afirmar sobre amostra que nao sustenta.
    """
    for placa in ("CCC3C33", "DDD4D44"):
        v = await _veiculo(db_session, placa)
        await _parte(db_session, v)

    res = await svc.apropriacao_por_equipamento(db_session)
    assert all(i["referencia_litros_por_hora"] is None for i in res["equipamentos"])
    assert res["fora_da_curva"] == []


async def test_equipamento_fora_da_curva_e_marcado(
    db_session: AsyncSession,
) -> None:
    """O caso de uso: achar o que consome demais perto dos pares."""
    # Tres pares normais a 5 L/h.
    for placa in ("EEE5E55", "FFF6F66", "GGG7G77"):
        v = await _veiculo(db_session, placa)
        await _parte(db_session, v, h_fim="10", litros="50", custo="250")
    # Um consumindo 10 L/h -- 100% acima da mediana.
    vazando = await _veiculo(db_session, "HHH8H88")
    await _parte(db_session, vazando, h_fim="10", litros="100", custo="500")

    res = await svc.apropriacao_por_equipamento(db_session)
    fora = res["fora_da_curva"]

    assert len(fora) == 1
    assert fora[0]["placa"] == "HHH8H88"
    assert fora[0]["referencia_litros_por_hora"] == Decimal("5")
    assert fora[0]["desvio_percentual"] == Decimal("100.0")
    # Os normais nao sao marcados.
    normais = [i for i in res["equipamentos"] if i["placa"] != "HHH8H88"]
    assert all(not i["alerta_custo"] for i in normais)


async def test_mediana_aguenta_o_outlier(db_session: AsyncSession) -> None:
    """Media seria envenenada pelo proprio caso que queremos achar.

    Com um equipamento a 50 L/h no grupo, a MEDIA sobe tanto que ele
    deixa de parecer fora da curva -- e esconde os demais junto.
    """
    for placa in ("III9I99", "JJJ1J11", "KKK2K22"):
        v = await _veiculo(db_session, placa)
        await _parte(db_session, v, h_fim="10", litros="50", custo="250")
    extremo = await _veiculo(db_session, "LLL3L33")
    await _parte(db_session, extremo, h_fim="10", litros="500", custo="2500")

    res = await svc.apropriacao_por_equipamento(db_session)
    marcados = {i["placa"] for i in res["fora_da_curva"]}

    # Mediana dos quatro (5, 5, 5, 50) = 5. So o extremo e marcado.
    assert marcados == {"LLL3L33"}


async def test_tipo_ausente_nao_vira_grupo(db_session: AsyncSession) -> None:
    """Agrupar 'sem tipo' misturaria escavadeira com caminhonete.

    Alerta falso ensina a pessoa a ignorar o painel.
    """
    for placa in ("MMM4M44", "NNN5N55", "OOO6O66"):
        v = await _veiculo(db_session, placa, tipo=None)
        await _parte(db_session, v, h_fim="10", litros="50")
    gastao = await _veiculo(db_session, "PPP7P77", tipo=None)
    await _parte(db_session, gastao, h_fim="10", litros="500")

    res = await svc.apropriacao_por_equipamento(db_session)
    assert res["fora_da_curva"] == []
    assert all(i["referencia_litros_por_hora"] is None for i in res["equipamentos"])


async def test_filtro_por_obra_e_periodo(db_session: AsyncSession) -> None:
    a = await _veiculo(db_session, "QQQ8Q88", obra="Obra A")
    b = await _veiculo(db_session, "RRR9R99", obra="Obra B")
    await _parte(db_session, a, dia=date(2026, 9, 10))
    await _parte(db_session, b, dia=date(2026, 9, 10))
    await _parte(db_session, a, dia=date(2026, 1, 5))

    so_obra_a = await svc.apropriacao_por_equipamento(db_session, obra="Obra A")
    assert [i["placa"] for i in so_obra_a["equipamentos"]] == ["QQQ8Q88"]
    assert so_obra_a["totais"]["apontamentos"] == 2

    setembro = await svc.apropriacao_por_equipamento(
        db_session, inicio=date(2026, 9, 1), fim=date(2026, 9, 30)
    )
    assert setembro["totais"]["apontamentos"] == 2


async def test_veiculo_medido_em_km_tambem_e_apropriado(
    db_session: AsyncSession,
) -> None:
    """A frota e mista -- caminhao tem odometro, nao horimetro."""
    v = await _veiculo(db_session, "SSS1S11", tipo="caminhao")
    await _parte(
        db_session, v, h_ini=None, h_fim=None, km_ini=1000, km_fim=1300,
        litros="100", custo="600",
    )

    res = await svc.apropriacao_por_equipamento(db_session)
    item = res["equipamentos"][0]

    assert item["km_rodados"] == 300
    assert item["consumo_km_por_litro"] == Decimal("3.000")
    assert item["custo_por_km"] == Decimal("2.00")
    # Sem horimetro, as metricas por hora ficam ausentes em vez de zero.
    assert item["consumo_litros_por_hora"] is None
    assert item["custo_por_hora"] is None


async def test_maior_custo_primeiro(db_session: AsyncSession) -> None:
    barato = await _veiculo(db_session, "TTT2T22")
    caro = await _veiculo(db_session, "UUU3U33")
    await _parte(db_session, barato, custo="100")
    await _parte(db_session, caro, custo="900")

    res = await svc.apropriacao_por_equipamento(db_session)
    assert [i["placa"] for i in res["equipamentos"]] == ["UUU3U33", "TTT2T22"]
