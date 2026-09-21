"""Modulo juridico -- contencioso trabalhista vindo do EasyJur.

O client e substituido por um duble que devolve as fixtures
anonimizadas. Nenhum teste toca o EasyJur real: cada login errado la
aproxima o bloqueio da conta de quem usa o sistema para trabalhar.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.juridico import service as svc
from app.modules.juridico.models import Andamento, Processo, SyncLog

pytestmark = pytest.mark.asyncio
FIX = Path(__file__).parent / "fixtures" / "easyjur"


class ClientFalso:
    """Devolve a fixture na pagina 1 e uma pagina vazia depois."""

    def __init__(self, *, html: str | None = None, csv: bytes | None = None):
        self.html = html or (FIX / "processos_pagina.html").read_text()
        self.csv = csv or (FIX / "andamentos_export.csv").read_bytes()
        self.paginas_pedidas: list[int] = []

    async def listar_processos(self, page: int = 1) -> str:
        self.paginas_pedidas.append(page)
        if page == 1:
            return self.html
        return '<table><tbody></tbody></table><h3>453  Registros Encontrados</h3>'

    async def exportar_andamentos_csv(self) -> bytes:
        return self.csv

    async def aclose(self) -> None:
        pass


async def _contar(db: AsyncSession, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_sincroniza_processos_e_andamentos(db_session: AsyncSession) -> None:
    r = await svc.sincronizar(db_session, ClientFalso(), source="manual")
    assert r.processos == 3
    assert r.andamentos == 3
    assert await _contar(db_session, Processo) == 3
    assert await _contar(db_session, Andamento) == 3


async def test_rodar_duas_vezes_nao_duplica(db_session: AsyncSession) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    assert await _contar(db_session, Processo) == 3
    assert await _contar(db_session, Andamento) == 3


async def test_andamento_liga_ao_processo_pelo_cnj(db_session: AsyncSession) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    ligados = (
        await db_session.execute(
            select(Andamento).where(Andamento.processo_id.is_not(None))
        )
    ).scalars().all()
    assert len(ligados) == 2  # os dois do CNJ ...0001


async def test_andamento_de_processo_desconhecido_entra_sem_vinculo(
    db_session: AsyncSession,
) -> None:
    """O feed cita processos por numero, e nem todo numero citado esta
    entre os processos listados. Descartar o andamento perderia
    movimentacao real; inventar o processo seria pior."""
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    orfao = (
        await db_session.execute(
            select(Andamento).where(Andamento.numero_cnj.like("0000009%"))
        )
    ).scalar_one()
    assert orfao.processo_id is None


async def test_divergencia_com_o_total_declarado_e_registrada(
    db_session: AsyncSession,
) -> None:
    """A fixture declara 453 e traz 3. O pull NAO pode aceitar isso em
    silencio -- foi uma resposta "vazia com cara de certa" que fez 453
    processos virarem 0 em 17/09/2026."""
    r = await svc.sincronizar(db_session, ClientFalso(), source="manual")
    assert r.total_declarado == 453
    assert r.divergencia is True
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.divergencia is True


async def test_manual_nao_queima_a_janela_do_beat(db_session: AsyncSession) -> None:
    """Mesmo precedente do ponto e do TOTVS: `source` faz parte da
    chave do log. Sem isso, um "sincronizar agora" as 10h faria o job
    agendado do dia achar que ja rodou."""
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    await svc.sincronizar(db_session, ClientFalso(), source="beat")
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    logs = (await db_session.execute(select(SyncLog))).scalars().all()
    assert sorted(x.source for x in logs) == ["beat", "manual"]


async def test_obra_nao_cadastrada_guarda_o_codigo_sem_inventar_obra(
    db_session: AsyncSession,
) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    p = (
        await db_session.execute(select(Processo).where(Processo.codigo_obra == "231"))
    ).scalar_one()
    assert p.obra_id is None


async def test_resumo_devolve_o_denominador_dos_campos_esparsos(
    db_session: AsyncSession,
) -> None:
    """ "Risco remoto em 100% dos processos" seria falso: o verdadeiro
    e "2 de 2 preenchidos, num universo de 3". Agregar campo esparso
    sem o denominador transforma cobertura baixa em afirmacao sobre a
    carteira inteira."""
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    resumo = await svc.resumo(db_session)
    assert resumo["total"] == 3
    risco = resumo["campos_esparsos"]["risco"]
    assert risco["preenchidos"] == 2
    assert risco["total"] == 3


async def test_api_lista_processos(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    r = await api_client.get("/api/v1/juridico/processos", headers=auth_headers)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["total"] == 3
    assert corpo["data"][0]["numero_cnj"]


async def test_api_exige_autenticacao(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/juridico/processos")).status_code == 401
