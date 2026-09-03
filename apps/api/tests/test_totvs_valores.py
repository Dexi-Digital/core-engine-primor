"""Normalizacao de valores monetarios vindos do RM (TOTVS).

O RM devolve valor como string em varios endpoints, e a formatacao
depende da cultura configurada no Host (`WebServiceCulture`). Nada
pode passar por `float` -- ver decisao #6 do Modulo C.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.valores import coerce_valor, coerce_valor_rm


def test_string_com_virgula_decimal_vira_decimal_exato():
    """RM com cultura pt-BR devolve "1234,56". O `_coerce_valor` atual
    (Decimal(str(v))) levanta InvalidOperation nesse caso."""
    assert coerce_valor_rm("1234,56") == Decimal("1234.56")


def test_string_pt_br_com_milhar_e_virgula():
    assert coerce_valor_rm("1.234.567,89") == Decimal("1234567.89")


def test_string_invariant_com_ponto_decimal_e_preservada():
    """Com `WebServiceCulture=Invariant` o RM devolve ponto decimal."""
    assert coerce_valor_rm("1234.56") == Decimal("1234.56")


def test_negativo_pt_br():
    assert coerce_valor_rm("-1.234,56") == Decimal("-1234.56")


def test_valor_ja_decimal_passa_intacto():
    assert coerce_valor_rm(Decimal("10.01")) == Decimal("10.01")


def test_float_nao_vira_ruido_binario():
    assert coerce_valor_rm(1234.1) == Decimal("1234.1")


def test_none_e_string_vazia_viram_none():
    assert coerce_valor_rm(None) is None
    assert coerce_valor_rm("") is None
    assert coerce_valor_rm("   ") is None


def test_string_com_espacos_e_nbsp_e_limpa():
    assert coerce_valor_rm("  1.234,56 ") == Decimal("1234.56")


def test_ponto_unico_com_tres_casas_e_lido_como_decimal():
    """AMBIGUIDADE documentada: "1.234" pode ser milhar pt-BR ou decimal
    invariant. Assumimos invariant (o que o Host devolve com
    `WebServiceCulture=Invariant`, que e o que vamos pedir ao time Cloud).
    Sem virgula na string, ponto E separador decimal -- sempre."""
    assert coerce_valor_rm("1.234") == Decimal("1.234")


def test_valor_nao_numerico_levanta_value_error():
    """Mesmo contrato do `_coerce_valor`: cai no 422 do modulo, nao 500."""
    with pytest.raises(ValueError):
        coerce_valor_rm("abc")


def test_coerce_valor_mantem_semantica_original():
    """Regressao: o helper reusado pelo financeiro_contratos nao muda."""
    assert coerce_valor("1234.1") == Decimal("1234.1")
    assert coerce_valor(None) is None
    with pytest.raises(ValueError):
        coerce_valor("1234,56")
