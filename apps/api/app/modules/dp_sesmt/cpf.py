"""Validacao local do CPF (algoritmo dos digitos verificadores).

Nao chama nenhuma API externa -- apenas verifica se a string tem 11
digitos e se os 2 ultimos batem com o calculo dos verificadores. Util
para descartar CPFs claramente invalidos antes de mandar para DirectData
(que cobra por consulta) ou para validar entrada de formulario.
"""
from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")


def normalize_cpf(cpf: str) -> str:
    return _DIGITS_RE.sub("", cpf or "")


def format_cpf(cpf: str) -> str:
    """Aplica mascara 000.000.000-00. Assume CPF ja normalizado."""
    n = normalize_cpf(cpf)
    if len(n) != 11:
        return cpf
    return f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}"


def is_valid_cpf(cpf: str) -> bool:
    """Verifica algoritmo dos digitos verificadores.

    Rejeita: comprimento != 11, todos digitos iguais (000..., 111...),
    digitos verificadores incorretos.
    """
    n = normalize_cpf(cpf)
    if len(n) != 11 or not n.isdigit():
        return False
    if n == n[0] * 11:
        # Sequencias 11111111111 / 22222222222 etc passam no algoritmo
        # mas sao invalidas por convencao.
        return False

    # Primeiro digito verificador.
    soma = sum(int(n[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    dv1 = 0 if resto < 2 else 11 - resto
    if dv1 != int(n[9]):
        return False

    # Segundo digito verificador.
    soma = sum(int(n[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    dv2 = 0 if resto < 2 else 11 - resto
    return dv2 == int(n[10])
