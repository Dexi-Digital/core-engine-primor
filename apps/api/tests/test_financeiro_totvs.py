"""Ingestao de lancamentos do TOTVS RM -- tabelas de destino + idempotencia."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.totvs.client import TotvsClient
from app.integrations.totvs.extractor import MockExtractor
from app.modules.financeiro_totvs.models import TotvsLancamento, TotvsSyncLog
from app.modules.financeiro_totvs.service import ingest_lancamentos

DESDE = date(2026, 8, 1)
ATE = date(2026, 8, 31)


def _client() -> TotvsClient:
    return TotvsClient(extractor=MockExtractor())


@pytest.mark.asyncio
async def test_ingest_grava_lancamentos(db_session: AsyncSession):
    resumo = await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE)
    assert resumo.lidos == 5
    assert resumo.gravados == 5
    linhas = (await db_session.scalars(select(TotvsLancamento))).all()
    assert len(linhas) == 5
    assert all(isinstance(row.valor, Decimal) for row in linhas)
    assert all(row.extractor == "totvs_mock" for row in linhas)


@pytest.mark.asyncio
async def test_rodar_duas_vezes_nao_duplica(db_session: AsyncSession):
    """Decisao #4: idempotencia copiando o PNCP -- external_id + ON CONFLICT."""
    await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE)
    await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE)
    linhas = (await db_session.scalars(select(TotvsLancamento))).all()
    assert len(linhas) == 5


@pytest.mark.asyncio
async def test_reingestao_atualiza_valor_alterado(db_session: AsyncSession):
    await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE)
    alvo = (await db_session.scalars(select(TotvsLancamento))).first()
    assert alvo is not None
    external_id, original = alvo.external_id, alvo.valor
    alvo.valor = Decimal("0.01")
    await db_session.commit()

    await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE)
    de_novo = await db_session.scalar(
        select(TotvsLancamento).where(TotvsLancamento.external_id == external_id)
    )
    assert de_novo is not None and de_novo.valor == original


# ------------------------------------------------------- decisao #5: `source`
@pytest.mark.asyncio
async def test_disparo_manual_nao_queima_a_janela_do_beat(db_session: AsyncSession):
    """Decisao #5 -- o bug do `dispatch_contrato_alerts_endpoint`.

    La, a unique key de `contratos_alertas_log` nao distingue origem, e
    um disparo manual SUPRIME o alerta do beat naquele dia. Aqui a
    unique key inclui `source` desde o primeiro commit, entao o botao
    "sincronizar agora" e o beat convivem na mesma janela.
    """
    await ingest_lancamentos(
        db_session, _client(), desde=DESDE, ate=ATE, source="manual"
    )
    resumo_beat = await ingest_lancamentos(
        db_session, _client(), desde=DESDE, ate=ATE, source="beat"
    )
    assert resumo_beat.skipped is False, "beat foi suprimido pelo disparo manual"

    logs = (await db_session.scalars(select(TotvsSyncLog))).all()
    assert {log.source for log in logs} == {"manual", "beat"}
    assert {log.janela for log in logs} == {"2026-08-01..2026-08-31"}


@pytest.mark.asyncio
async def test_mesma_origem_e_janela_atualiza_o_log_in_place(db_session: AsyncSession):
    await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE, source="beat")
    await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE, source="beat")
    logs = (await db_session.scalars(select(TotvsSyncLog))).all()
    assert len(logs) == 1
    assert logs[0].lidos == 5


@pytest.mark.asyncio
async def test_log_registra_falha_sem_derrubar_a_transacao(db_session: AsyncSession):
    """Pull que estoura precisa deixar rastro -- senao a falha das 3h da
    manha some e ninguem descobre ate o mes fechar errado."""
    from app.integrations.totvs.extractor import TotvsPermissionError

    class Explodindo:
        source = "totvs_rest"

        async def health_check(self) -> bool:
            return False

        async def aclose(self) -> None:
            return None

        async def fetch_lancamentos(self, *, desde, ate):
            raise TotvsPermissionError("perfil sem acesso")

    with pytest.raises(TotvsPermissionError):
        await ingest_lancamentos(
            db_session, TotvsClient(extractor=Explodindo()), desde=DESDE, ate=ATE
        )

    (log,) = (await db_session.scalars(select(TotvsSyncLog))).all()
    assert log.status == "failed"
    assert "perfil sem acesso" in (log.error_message or "")


@pytest.mark.asyncio
async def test_nao_cria_coluna_totvs_em_contrato():
    """Decisao #7: dados do TOTVS moram em tabela propria; reconciliacao
    por documento. Mesmo precedente da assinatura digital (models.py, 04/08)."""
    from app.modules.financeiro_contratos.models import Contrato

    assert not [c for c in Contrato.__table__.columns if c.name.startswith("totvs")]


# ------------------------------------------- guarda do filtro por coligada
@pytest.mark.asyncio
async def test_pull_falha_se_o_perfil_nao_enxerga_todas_as_coligadas(
    db_session: AsyncSession,
):
    """Confirmado pela TOTVS em 27/08/2026: perfil sem permissao numa
    coligada NAO da erro -- vira filtro, e a API devolve 200 com apenas
    parte dos registros. Sem esta guarda, a reconciliacao fecharia
    "certo" em cima de metade dos lancamentos.
    """
    from app.integrations.totvs.extractor import TotvsPermissionError

    class ExtractorMiope:
        """Enxerga so a coligada 1; esperamos 1 e 2."""

        source = "totvs_rest"

        async def health_check(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

        async def list_coligadas(self) -> set[int]:
            return {1}

        async def fetch_lancamentos(self, *, desde, ate):
            return []

    with pytest.raises(TotvsPermissionError, match="coligada"):
        await ingest_lancamentos(
            db_session,
            TotvsClient(extractor=ExtractorMiope()),
            desde=DESDE,
            ate=ATE,
            coligadas_esperadas={1, 2},
        )

    (log,) = (await db_session.scalars(select(TotvsSyncLog))).all()
    assert log.status == "failed"


@pytest.mark.asyncio
async def test_pull_segue_quando_todas_as_coligadas_estao_visiveis(
    db_session: AsyncSession,
):
    class ExtractorCompleto:
        source = "totvs_rest"

        async def health_check(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

        async def list_coligadas(self) -> set[int]:
            return {1, 2, 7}

        async def fetch_lancamentos(self, *, desde, ate):
            return []

    resumo = await ingest_lancamentos(
        db_session,
        TotvsClient(extractor=ExtractorCompleto()),
        desde=DESDE,
        ate=ATE,
        coligadas_esperadas={1, 2},
    )
    assert resumo.lidos == 0


@pytest.mark.asyncio
async def test_sem_coligadas_esperadas_a_guarda_nao_roda(db_session: AsyncSession):
    """Guarda e opt-in: so vale quando o operador declarou o que esperar."""
    resumo = await ingest_lancamentos(db_session, _client(), desde=DESDE, ate=ATE)
    assert resumo.lidos == 5
