"""Planos de manutencao -- vencimento medido em USO, nao em calendario.

A regra tem tres armadilhas que os testes cobrem: dizer "em dia" sobre
equipamento sem leitura, contar do lugar errado, e deixar equipamento
sem plano invisivel no painel.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.manutencao_frota import planos as svc
from app.modules.manutencao_frota.models import (
    PARTE_PENDENTE,
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

pytestmark = pytest.mark.asyncio


async def _veiculo(db: AsyncSession, placa="AAA1A11", *, km_atual=None, tipo="escavadeira"):
    v = Veiculo(placa=placa, tipo=tipo, modelo="X", obra="Obra A", km_atual=km_atual)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _plano(
    db: AsyncSession, v: Veiculo, *, base=PLANO_BASE_HORAS,
    intervalo="50", marcador="100", descricao="Troca de oleo", ativo=True,
):
    p = PlanoManutencao(
        veiculo_id=v.id, descricao=descricao, base=base,
        intervalo=Decimal(intervalo),
        ultima_revisao_marcador=Decimal(marcador) if marcador is not None else None,
        ultima_revisao_em=date(2026, 8, 1), ativo=ativo,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _parte(db: AsyncSession, v: Veiculo, *, h_fim=None, km_fim=None,
                 status=PARTE_REVISADO):
    db.add(ParteDiaria(
        veiculo_id=v.id, data=date(2026, 9, 10),
        horimetro_inicio=Decimal("0") if h_fim is not None else None,
        horimetro_fim=Decimal(str(h_fim)) if h_fim is not None else None,
        km_inicio=0 if km_fim is not None else None, km_fim=km_fim,
        ocr_status=status,
    ))
    await db.commit()


# --- A regra (funcao pura) --------------------------------------------------


async def test_sem_leitura_nao_diz_em_dia(db_session: AsyncSession) -> None:
    """O erro mais perigoso: afirmar "ok" sobre o que nao se mediu.

    Equipamento que nunca apontou nao esta em dia -- nao se sabe. Um
    "ok" aqui seria manutencao vencida passando como saudavel.
    """
    v = await _veiculo(db_session)
    p = await _plano(db_session, v)
    st = svc.avaliar(p, v.placa, None)
    assert st.status == PLANO_SEM_LEITURA
    assert st.falta is None
    assert st.proxima_em is None


async def test_sem_marcador_da_ultima_revisao_tambem_nao_afirma(
    db_session: AsyncSession,
) -> None:
    """Sem saber de onde contar, nao ha o que calcular."""
    v = await _veiculo(db_session)
    p = await _plano(db_session, v, marcador=None)
    st = svc.avaliar(p, v.placa, Decimal("500"))
    assert st.status == PLANO_SEM_LEITURA


async def test_conta_do_marcador_da_revisao_nao_do_zero(
    db_session: AsyncSession,
) -> None:
    """Proxima = ultima revisao + intervalo.

    Contar do zero faria a revisao vencer a cada multiplo absoluto,
    ignorando que ela ja foi feita -- exatamente o erro que o gatilho
    global nao consegue evitar.
    """
    v = await _veiculo(db_session)
    p = await _plano(db_session, v, intervalo="50", marcador="100")
    st = svc.avaliar(p, v.placa, Decimal("120"))
    assert st.proxima_em == Decimal("150")
    assert st.falta == Decimal("30")
    assert st.status == PLANO_OK


async def test_vencida_quando_passou(db_session: AsyncSession) -> None:
    v = await _veiculo(db_session)
    p = await _plano(db_session, v, intervalo="50", marcador="100")
    st = svc.avaliar(p, v.placa, Decimal("155"))
    assert st.status == PLANO_VENCIDA
    assert st.falta == Decimal("-5")


async def test_proxima_usa_fracao_do_intervalo(db_session: AsyncSession) -> None:
    """10% do intervalo: 5 h num plano de 50 h, 300 km num de 3.000.

    Um limite fixo em numero nao serviria para as duas bases -- 300
    "unidades" de aviso seriam 6 intervalos inteiros de um plano de 50 h.
    """
    v = await _veiculo(db_session)
    p50 = await _plano(db_session, v, intervalo="50", marcador="100")
    # falta 5 h de 50 -> exatamente no limite, ja avisa
    assert svc.avaliar(p50, v.placa, Decimal("145")).status == PLANO_PROXIMA
    # falta 6 h -> ainda ok
    assert svc.avaliar(p50, v.placa, Decimal("144")).status == PLANO_OK

    v2 = await _veiculo(db_session, "BBB2B22", tipo="caminhao")
    p3000 = await _plano(db_session, v2, base=PLANO_BASE_KM,
                         intervalo="3000", marcador="10000")
    assert svc.avaliar(p3000, v2.placa, Decimal("12700")).status == PLANO_PROXIMA
    assert svc.avaliar(p3000, v2.placa, Decimal("12699")).status == PLANO_OK


# --- Leitura corrente -------------------------------------------------------


async def test_leitura_vem_do_maior_apontamento(db_session: AsyncSession) -> None:
    v = await _veiculo(db_session)
    await _parte(db_session, v, h_fim=120)
    await _parte(db_session, v, h_fim=180)
    await _parte(db_session, v, h_fim=150)
    assert await svc.leitura_atual(db_session, v, PLANO_BASE_HORAS) == Decimal("180")


async def test_apontamento_pendente_nao_conta_como_leitura(
    db_session: AsyncSession,
) -> None:
    v = await _veiculo(db_session)
    await _parte(db_session, v, h_fim=120)
    await _parte(db_session, v, h_fim=900, status=PARTE_PENDENTE)
    assert await svc.leitura_atual(db_session, v, PLANO_BASE_HORAS) == Decimal("120")


async def test_km_atual_do_cadastro_e_piso(db_session: AsyncSession) -> None:
    """Escolher o menor adiaria a revisao -- e adiar e o erro caro.

    O `km_atual` e digitado a mao e as vezes esta mais novo que a
    ultima parte diaria processada.
    """
    v = await _veiculo(db_session, km_atual=50000, tipo="caminhao")
    await _parte(db_session, v, km_fim=48000)
    assert await svc.leitura_atual(db_session, v, PLANO_BASE_KM) == Decimal("50000")


# --- Painel -----------------------------------------------------------------


async def test_vencidas_primeiro(db_session: AsyncSession) -> None:
    v = await _veiculo(db_session)
    await _parte(db_session, v, h_fim=200)
    await _plano(db_session, v, intervalo="50", marcador="180", descricao="Filtro")   # falta 30 -> ok
    await _plano(db_session, v, intervalo="50", marcador="100", descricao="Oleo")     # vencida
    await _plano(db_session, v, intervalo="50", marcador="155", descricao="Correia")  # falta 5 -> proxima

    res = await svc.status_dos_planos(db_session)
    ordem = [p["descricao"] for p in res["planos"]]
    assert ordem[0] == "Oleo"
    assert ordem[1] == "Correia"
    assert res["por_status"][PLANO_VENCIDA] == 1
    assert len(res["vencidas"]) == 1


async def test_varios_planos_por_equipamento(db_session: AsyncSession) -> None:
    """Oleo e filtro sao dois planos do MESMO veiculo."""
    v = await _veiculo(db_session)
    await _plano(db_session, v, descricao="Oleo", intervalo="250")
    await _plano(db_session, v, descricao="Filtro", intervalo="500")
    res = await svc.status_dos_planos(db_session)
    assert res["total"] == 2


async def test_plano_inativo_fica_fora(db_session: AsyncSession) -> None:
    v = await _veiculo(db_session)
    await _plano(db_session, v, ativo=False)
    res = await svc.status_dos_planos(db_session)
    assert res["total"] == 0


async def test_equipamento_sem_plano_aparece(db_session: AsyncSession) -> None:
    """A ausencia tem que ser visivel.

    Sem esta lista, equipamento sem plano fica invisivel e "nenhum
    alerta" e lido como "tudo em dia" -- o pior modo de falha de um
    controle de manutencao.
    """
    com = await _veiculo(db_session, "CCC3C33")
    await _plano(db_session, com)
    sem = await _veiculo(db_session, "DDD4D44")

    lista = await svc.veiculos_sem_plano(db_session)
    placas = {v["placa"] for v in lista}
    assert sem.placa in placas
    assert com.placa not in placas


# --- Registro de revisao ----------------------------------------------------


async def test_registrar_revisao_recomeca_a_contagem(
    db_session: AsyncSession,
) -> None:
    v = await _veiculo(db_session)
    await _parte(db_session, v, h_fim=160)
    p = await _plano(db_session, v, intervalo="50", marcador="100")
    assert svc.avaliar(p, v.placa, Decimal("160")).status == PLANO_VENCIDA

    p = await svc.registrar_revisao(
        db_session, p, marcador=None, data_revisao=date(2026, 9, 12),
        actor="teste@primor.com",
    )
    # Sem marcador informado, caiu na leitura corrente (160).
    assert p.ultima_revisao_marcador == Decimal("160")
    assert svc.avaliar(p, v.placa, Decimal("160")).status == PLANO_OK
    assert svc.avaliar(p, v.placa, Decimal("160")).proxima_em == Decimal("210")


async def test_revisao_sem_marcador_e_sem_leitura_recusa(
    db_session: AsyncSession,
) -> None:
    """Falhar pedindo o numero e melhor que gravar contagem errada."""
    v = await _veiculo(db_session)
    p = await _plano(db_session, v)
    with pytest.raises(ValueError, match="horimetro/odometro"):
        await svc.registrar_revisao(
            db_session, p, marcador=None, actor="teste@primor.com"
        )


# --- HTTP -------------------------------------------------------------------


async def test_crud_e_revisao_via_http(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    v = await _veiculo(db_session, "EEE5E55")
    await _parte(db_session, v, h_fim=300)

    r = await api_client.post(
        "/api/v1/manutencao-frota/planos-manutencao",
        json={
            "veiculo_id": v.id, "descricao": "Troca de oleo",
            "base": "horas", "intervalo": "250",
            "ultima_revisao_marcador": "100",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    plano_id = r.json()["id"]

    r = await api_client.get(
        "/api/v1/manutencao-frota/planos-manutencao", headers=auth_headers
    )
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 1
    assert corpo["planos"][0]["leitura_atual"] == "300.00"
    assert "sem_plano" in corpo

    r = await api_client.post(
        f"/api/v1/manutencao-frota/planos-manutencao/{plano_id}/revisao",
        json={"data": "2026-09-15"}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ultima_revisao_marcador"] == "300.00"


async def test_base_invalida_recusada(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    v = await _veiculo(db_session, "FFF6F66")
    r = await api_client.post(
        "/api/v1/manutencao-frota/planos-manutencao",
        json={"veiculo_id": v.id, "descricao": "Qualquer",
              "base": "meses", "intervalo": "6"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "km" in r.text and "horas" in r.text


async def test_planos_exige_autenticacao(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/manutencao-frota/planos-manutencao")
    assert r.status_code == 401
