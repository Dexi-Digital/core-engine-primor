"""Utilitarios de CPF compartilhados (validacao local, sem API externa).

Vivem em `app/core` porque tanto modules (dp_sesmt) quanto integrations
(onsafety) precisam deles, e integrations nao pode importar de modules
(camada invertida). `app/modules/dp_sesmt/cpf.py` re-exporta daqui por
compatibilidade.
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


def mask_cpf(cpf: str) -> str:
    """Mascara LGPD para logs/auditoria: `***.456.789-**`.

    Mantem so os 6 digitos centrais (padrao de publicacao de CPF em
    atos oficiais) -- suficiente para conferencia humana sem expor o
    documento inteiro. Entrada sem 11 digitos vira `***` (nunca ecoa
    o valor cru, que pode ser um CPF digitado com erro).
    """
    n = normalize_cpf(cpf)
    if len(n) != 11:
        return "***"
    return f"***.{n[3:6]}.{n[6:9]}-**"


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
