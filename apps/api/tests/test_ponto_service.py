"""Ingestao do ponto (Solides/Tangerino) -- locais, funcionarios, batidas."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.tangerino.client import TangerinoClient
from app.modules.dp_sesmt.models import Employee
from app.modules.obras.models import Obra
from app.modules.ponto.models import (
    PontoBatida,
    PontoFuncionario,
    PontoLocalTrabalho,
    PontoSyncLog,
)
from app.modules.ponto.service import (
    ingest_batidas,
    ingest_funcionarios,
    ingest_locais_trabalho,
)

DESDE = date(2026, 8, 1)
ATE = date(2026, 8, 31)


def _client() -> TangerinoClient:
    """Sem token -> mock deterministico do proprio adapter."""
    return TangerinoClient(api_token=None)


async def _obra(db: AsyncSession, codigo: str, nome: str) -> Obra:
    obra = Obra(codigo=codigo, nome=nome)
    db.add(obra)
    await db.commit()
    await db.refresh(obra)
    return obra


# ------------------------------------------------------------ locais
@pytest.mark.asyncio
async def test_ingest_locais_grava_e_e_idempotente(db_session: AsyncSession):
    r1 = await ingest_locais_trabalho(db_session, _client())
    assert r1.gravados > 0
    r2 = await ingest_locais_trabalho(db_session, _client())
    locais = (await db_session.scalars(select(PontoLocalTrabalho))).all()
    assert len(locais) == r1.gravados == r2.gravados


@pytest.mark.asyncio
async def test_local_de_obra_liga_na_obra_existente(db_session: AsyncSession):
    """O vinculo funcionario -> obra sai daqui (ADR-003, resolvido com
    a API real: o local de trabalho E a obra)."""
    obra = await _obra(db_session, "243", "Obra 243")

    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_locais_trabalho(self, *, page=0, size=100):
            if page:
                return {"items": [], "total": 1, "source": "tangerino"}
            return {
                "items": [
                    {"id": 9001, "external_id": None, "nome": "Obra 243",
                     "ativo": True, "padrao": False}
                ],
                "total": 1,
                "source": "tangerino",
            }

    await ingest_locais_trabalho(db_session, Um())
    local = await db_session.scalar(select(PontoLocalTrabalho))
    assert local is not None
    assert local.codigo_obra == "243"
    assert local.obra_id == obra.id


@pytest.mark.asyncio
async def test_local_administrativo_fica_sem_obra(db_session: AsyncSession):
    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_locais_trabalho(self, *, page=0, size=100):
            if page:
                return {"items": [], "total": 1, "source": "tangerino"}
            return {
                "items": [
                    {"id": 9002, "external_id": None, "nome": "ADM PRIMOR",
                     "ativo": True, "padrao": False}
                ],
                "total": 1,
                "source": "tangerino",
            }

    await ingest_locais_trabalho(db_session, Um())
    local = await db_session.scalar(select(PontoLocalTrabalho))
    assert local is not None
    assert local.codigo_obra is None
    assert local.obra_id is None


@pytest.mark.asyncio
async def test_local_de_obra_sem_obra_cadastrada_fica_pendente(
    db_session: AsyncSession,
):
    """Nao inventamos obra. Fica com o codigo extraido e sem `obra_id`
    -- a tela mostra como pendencia de vinculo."""

    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_locais_trabalho(self, *, page=0, size=100):
            if page:
                return {"items": [], "total": 1, "source": "tangerino"}
            return {
                "items": [
                    {"id": 9003, "external_id": None, "nome": "Obra 999",
                     "ativo": True, "padrao": False}
                ],
                "total": 1,
                "source": "tangerino",
            }

    await ingest_locais_trabalho(db_session, Um())
    local = await db_session.scalar(select(PontoLocalTrabalho))
    assert local is not None
    assert local.codigo_obra == "999"
    assert local.obra_id is None


# ------------------------------------------------------- funcionarios
@pytest.mark.asyncio
async def test_funcionario_liga_no_dp_employee_por_cpf(db_session: AsyncSession):
    emp = Employee(
        cpf="52998224725", nome_completo="Fulano de Tal", cargo="Pedreiro"
    )
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)

    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_funcionarios(self, *, page=0, size=100, incluir_demitidos=True):
            if page:
                return {"items": [], "total": 1, "source": "tangerino"}
            return {
                "items": [
                    {
                        "id": 6929539,
                        "external_id": None,
                        "nome": "Fulano de Tal",
                        "cpf": "52998224725",
                        "pis": "12345678901",
                        # epoch em MILISSEGUNDOS (confirmado na API real)
                        "admissao": 1787022000000,
                        "demitido": False,
                        "workplaces": [{"id": 9001, "nome": "Obra 243"}],
                    }
                ],
                "total": 1,
                "source": "tangerino",
            }

    await ingest_funcionarios(db_session, Um())
    pf = await db_session.scalar(select(PontoFuncionario))
    assert pf is not None
    assert pf.employee_id == emp.id
    assert pf.cpf == "52998224725"
    assert pf.local_trabalho_externo_id == 9001
    # epoch ms -> date, nao 1970 nem 56 mil anos no futuro
    assert pf.data_admissao is not None
    assert pf.data_admissao.year == 2026


@pytest.mark.asyncio
async def test_funcionario_sem_cpf_no_dp_fica_sem_vinculo(db_session: AsyncSession):
    """Nao cria funcionario no DP a partir do ponto -- o cadastro e a
    fonte da verdade. Fica sem vinculo e visivel como pendencia."""

    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_funcionarios(self, *, page=0, size=100, incluir_demitidos=True):
            if page:
                return {"items": [], "total": 1, "source": "tangerino"}
            return {
                "items": [
                    {"id": 777, "external_id": None, "nome": "Desconhecido",
                     "cpf": "11144477735", "pis": None, "admissao": None,
                     "demitido": False, "workplaces": []}
                ],
                "total": 1,
                "source": "tangerino",
            }

    await ingest_funcionarios(db_session, Um())
    pf = await db_session.scalar(select(PontoFuncionario))
    assert pf is not None
    assert pf.employee_id is None
    assert (await db_session.scalar(select(Employee))) is None


# ------------------------------------------------------------ batidas
@pytest.mark.asyncio
async def test_ingest_batidas_converte_epoch_ms_e_e_idempotente(
    db_session: AsyncSession,
):
    db_session.add(
        PontoFuncionario(tangerino_id=7, nome="Fulano", cpf="52998224725")
    )
    await db_session.commit()

    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_batidas(self, employee_id, *, start_date, end_date,
                               page=0, size=100):
            if page:
                return {"items": [], "total": 1, "source": "tangerino"}
            return {
                "items": [
                    {
                        "employee_id": 7,
                        "employee_external_id": "E-7",
                        "data_trabalho_ts": 1754006400000,
                        "inicio_ts": 1754032800000,
                        "fim_ts": 1754065200000,
                        "segundos_trabalhados": 28800,
                        "status": "CLOSED",
                        "pis": "12345678901",
                    }
                ],
                "total": 1,
                "source": "tangerino",
            }

    r1 = await ingest_batidas(db_session, Um(), desde=DESDE, ate=ATE)
    assert r1.gravados == 1
    await ingest_batidas(db_session, Um(), desde=DESDE, ate=ATE)
    batidas = (await db_session.scalars(select(PontoBatida))).all()
    assert len(batidas) == 1
    b = batidas[0]
    assert isinstance(b.inicio, datetime)
    assert b.inicio.year == 2025
    assert b.segundos_trabalhados == 28800


@pytest.mark.asyncio
async def test_sync_log_separa_beat_de_manual(db_session: AsyncSession):
    """Mesmo precedente do TOTVS e do bug dos alertas de contrato:
    `source` na unique key desde o primeiro commit."""
    await ingest_locais_trabalho(db_session, _client(), source="manual")
    await ingest_locais_trabalho(db_session, _client(), source="beat")
    logs = (await db_session.scalars(select(PontoSyncLog))).all()
    assert {log.source for log in logs} == {"manual", "beat"}


@pytest.mark.asyncio
async def test_busca_tudo_em_uma_requisicao_e_avisa_se_truncar(
    db_session: AsyncSession, caplog
):
    """A API do Solides NAO pagina -- confirmado em 28/08/2026: page,
    offset, start e pageNumber devolvem sempre os mesmos registros. So
    `size` funciona. Entao pedimos tudo de uma vez; se ainda assim o
    total for maior que o recebido, a leitura esta truncada e isso
    PRECISA aparecer no log em vez de virar dado faltando em silencio.
    """
    import logging

    chamadas: list[int] = []

    class Truncando:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_locais_trabalho(self, *, page=0, size=100):
            chamadas.append(size)
            return {
                "items": [
                    {"id": 1, "nome": "Obra 100", "ativo": True, "padrao": False}
                ],
                "total": 999,  # API diz que ha 999, devolveu 1
                "source": "tangerino",
            }

    with caplog.at_level(logging.WARNING):
        await ingest_locais_trabalho(db_session, Truncando())

    assert len(chamadas) == 1, "nao deve haver laco de paginacao"
    assert chamadas[0] >= 500, "precisa pedir tudo de uma vez"
    assert any("truncad" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_funcionarios_traz_so_ativos_por_default(db_session: AsyncSession):
    """A API nao pagina, limita `size` a 2000 no servidor, e devolve os
    DEMITIDOS primeiro. Pedir tudo na conta da Primor (2400) traria 2000
    demitidos e nenhum ativo -- medido em 28/08/2026. O default precisa
    ser ativos-only, senao o pull de batidas fica vazio."""
    visto: dict = {}

    class Um:
        is_mock = False

        async def aclose(self) -> None: ...

        async def list_funcionarios(self, *, page=0, size=100, incluir_demitidos=False):
            visto["incluir_demitidos"] = incluir_demitidos
            return {"items": [], "total": 0, "source": "tangerino"}

    await ingest_funcionarios(db_session, Um())
    assert visto["incluir_demitidos"] is False
