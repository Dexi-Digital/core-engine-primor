"""STATUSLAN e baixas da FLAN -- respondido pela TOTVS em 21/09/2026.

Ate aqui o adapter gravava `status_rm` bruto SEM interpretar, de
proposito: assumir uma ordem errada produziria relatorio financeiro
errado com cara de certo (o mesmo risco do `resultadoAso`). A resposta
do ticket 30268517 trouxe o dominio oficial:

    0 Em Aberto · 1 Baixado · 2 Cancelado · 3 Baixado por Acordo
    4 Baixado Parcialmente · 5 Bordero

E o valor efetivamente pago vem de FLANBAIXA.VALORBAIXADO, somado por
CODCOLIGADA + IDLAN -- VALORORIGINAL e so o valor de origem.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.totvs import extractor as ext
from app.integrations.totvs.client import TotvsClient
from app.integrations.totvs.extractor import MockExtractor
from app.modules.financeiro_totvs.models import TotvsLancamento
from app.modules.financeiro_totvs.service import ingest_lancamentos


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        (0, "em_aberto"),
        ("0", "em_aberto"),
        (1, "baixado"),
        (2, "cancelado"),
        (3, "baixado_por_acordo"),
        (4, "baixado_parcialmente"),
        (5, "bordero"),
    ],
)
def test_dominio_oficial_do_statuslan(bruto, esperado) -> None:
    assert ext.interpretar_statuslan(bruto) == esperado


def test_statuslan_desconhecido_nao_e_adivinhado() -> None:
    """Valor fora do dominio vira None, nunca "em_aberto" por default.

    Um status novo do RM (customizacao, versao futura) precisa aparecer
    como desconhecido -- nao como quitado nem como aberto."""
    assert ext.interpretar_statuslan(9) is None
    assert ext.interpretar_statuslan(None) is None
    assert ext.interpretar_statuslan("x") is None


def test_quitado_reune_baixado_e_acordo_mas_nao_parcial() -> None:
    """Para conciliacao: 1 e 3 estao quitados; 4 ainda tem saldo; 2 sai."""
    assert ext.situacao_quitada("baixado") is True
    assert ext.situacao_quitada("baixado_por_acordo") is True
    assert ext.situacao_quitada("baixado_parcialmente") is False
    assert ext.situacao_quitada("em_aberto") is False
    assert ext.situacao_quitada("cancelado") is False


def test_normalize_traz_situacao_e_valor_baixado() -> None:
    env = ext.normalize_lancamento(
        {
            "CODCOLIGADA": 1, "CODFILIAL": 1, "IDLAN": 10,
            "VALORORIGINAL": "1000,00", "VALORBAIXADO": "400,00",
            "STATUSLAN": 4, "DATAVENCIMENTO": "2026-09-30",
        },
        source="t",
    )
    assert env["status_rm"] == "4"            # bruto continua gravado
    assert env["situacao"] == "baixado_parcialmente"
    assert env["valor_baixado"] == Decimal("400.00")
    assert env["saldo"] == Decimal("600.00")


def test_sem_baixa_o_saldo_e_o_valor_original() -> None:
    env = ext.normalize_lancamento(
        {"CODCOLIGADA": 1, "IDLAN": 11, "VALORORIGINAL": "250,00", "STATUSLAN": 0},
        source="t",
    )
    assert env["valor_baixado"] is None
    assert env["saldo"] == Decimal("250.00")


def test_cancelado_nao_tem_saldo() -> None:
    """Cancelado nao e divida: a TOTVS orienta desconsiderar na conciliacao."""
    env = ext.normalize_lancamento(
        {"CODCOLIGADA": 1, "IDLAN": 12, "VALORORIGINAL": "250,00", "STATUSLAN": 2},
        source="t",
    )
    assert env["situacao"] == "cancelado"
    assert env["saldo"] == Decimal("0")


@pytest.mark.asyncio
async def test_ingestao_persiste_situacao_e_baixa(db_session: AsyncSession) -> None:
    await ingest_lancamentos(
        db_session, TotvsClient(extractor=MockExtractor()),
        desde=date(2026, 8, 1), ate=date(2026, 8, 31),
    )
    linhas = (await db_session.scalars(select(TotvsLancamento))).all()
    assert linhas
    assert all(row.situacao is not None for row in linhas), "mock sem STATUSLAN?"
    assert any(row.valor_baixado is not None for row in linhas), "mock sem baixa?"
    assert all(row.saldo is not None for row in linhas)
