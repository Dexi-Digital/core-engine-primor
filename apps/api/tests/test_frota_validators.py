"""Testes dos validadores locais de placa, renavam e chassi."""
from __future__ import annotations

import pytest

from app.modules.manutencao_frota.validators import (
    is_placa_mercosul,
    is_valid_chassi,
    is_valid_placa,
    is_valid_renavam,
    normalize_placa,
    normalize_renavam,
)

# --- placa ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ABC-1234", "ABC1234"),
        ("abc1234", "ABC1234"),
        ("  abc 1234 ", "ABC1234"),
        ("ABC1D23", "ABC1D23"),
    ],
)
def test_normalize_placa(raw: str, expected: str) -> None:
    assert normalize_placa(raw) == expected


@pytest.mark.parametrize(
    "placa",
    [
        "ABC1234",  # antiga
        "ABC1D23",  # Mercosul
        "abc-1234",  # case insensitive + separador
    ],
)
def test_placa_valida(placa: str) -> None:
    assert is_valid_placa(placa) is True


@pytest.mark.parametrize(
    "placa",
    [
        "AB1234",  # 6 chars
        "ABCD123",  # 4 letras + 3 num
        "ABC12345",  # 8 chars
        "1234567",  # so numeros
        "ABCDEFG",  # so letras
        "ABC12A3",  # padrao errado de mercosul
        "",
    ],
)
def test_placa_invalida(placa: str) -> None:
    assert is_valid_placa(placa) is False


def test_distinguir_mercosul_de_antiga() -> None:
    assert is_placa_mercosul("ABC1D23") is True
    assert is_placa_mercosul("ABC1234") is False


# --- renavam ----------------------------------------------------------------


def test_normalize_renavam_pad_zero() -> None:
    """Renavam legado de 9 digitos deve virar 11 com zero-padding."""
    assert normalize_renavam("123456789") == "00123456789"
    assert normalize_renavam("12345678901") == "12345678901"


def test_renavam_valido() -> None:
    # Renavam real (gerado por algoritmo de DV).
    # Pesos 3,2,9,8,7,6,5,4,3,2; soma * 10 % 11; >=10 -> 0; senao -> resto.
    # Para a sequencia 1234567890, soma = 1*3+2*2+3*9+4*8+5*7+6*6+7*5+8*4+9*3+0*2
    # = 3+4+27+32+35+36+35+32+27+0 = 231. 231*10 % 11 = 2310 % 11 = 0.
    # Portanto DV = 0 -> 12345678900 e valido.
    assert is_valid_renavam("12345678900") is True


def test_renavam_invalido_dv() -> None:
    # Mesma sequencia com DV trocado para 1.
    assert is_valid_renavam("12345678901") is False


def test_renavam_invalido_sequencia_repetida() -> None:
    assert is_valid_renavam("00000000000") is False


def test_renavam_invalido_curto() -> None:
    # 8 digitos -- nem mesmo o pad de 9 alcanca.
    assert is_valid_renavam("12345678") is False


# --- chassi -----------------------------------------------------------------


def test_chassi_valido_iso_3779() -> None:
    # 17 alfanumericos sem I/O/Q.
    assert is_valid_chassi("9BWZZZ377VT004251") is True


def test_chassi_loose_aceita_io_q() -> None:
    # Chassis brasileiros antigos as vezes tem I/O/Q -- default loose.
    assert is_valid_chassi("9BWQZZ377VTO04251") is True


def test_chassi_strict_rejeita_io_q() -> None:
    assert is_valid_chassi("9BWQZZ377VTO04251", strict=True) is False


def test_chassi_curto_invalido() -> None:
    assert is_valid_chassi("ABC123") is False


def test_chassi_caracteres_invalidos() -> None:
    assert is_valid_chassi("9BWZZZ377VT00425!") is False
