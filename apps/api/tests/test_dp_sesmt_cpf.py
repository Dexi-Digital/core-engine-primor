"""Validador local de CPF -- algoritmo dos digitos verificadores."""
from __future__ import annotations

import pytest

from app.modules.dp_sesmt.cpf import format_cpf, is_valid_cpf, normalize_cpf


@pytest.mark.parametrize(
    "cpf",
    [
        "111.444.777-35",  # valido classico
        "11144477735",
        "390.533.447-05",  # CPF valido publico (gerador)
    ],
)
def test_is_valid_cpf_aceita_cpfs_validos(cpf: str) -> None:
    assert is_valid_cpf(cpf) is True


@pytest.mark.parametrize(
    "cpf",
    [
        "",
        "123",
        "12345678901",  # digitos verificadores errados
        "111.444.777-34",  # 1 digito alterado
        "abc.def.ghi-jk",
        "11111111111",  # sequencia repetida -- invalida por convencao
        "00000000000",
        "99999999999",
    ],
)
def test_is_valid_cpf_rejeita_invalidos(cpf: str) -> None:
    assert is_valid_cpf(cpf) is False


def test_normalize_cpf_remove_mascara() -> None:
    assert normalize_cpf("111.444.777-35") == "11144477735"
    assert normalize_cpf("  111 444 777-35 ") == "11144477735"


def test_format_cpf_aplica_mascara() -> None:
    assert format_cpf("11144477735") == "111.444.777-35"


def test_format_cpf_passthrough_se_invalido() -> None:
    # Comportamento explicito: se nao tiver 11 digitos, devolve como veio.
    assert format_cpf("123") == "123"
