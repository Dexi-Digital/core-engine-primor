"""Validadores locais para campos da frota (placa, renavam, chassi).

Sem chamadas externas -- apenas formato + DV. Util para descartar
entradas claramente invalidas antes de gravar no DB ou disparar a RPA
do Detran (B.3 -- vai cobrar por consulta no SP).
"""
from __future__ import annotations

import re

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")
_DIGITS_RE = re.compile(r"\D+")

# Placa antiga (Brasil ate 2018): AAA0000 -- 3 letras + 4 numeros.
# Placa Mercosul (Brasil >= 2018): AAA0A00 -- 3 letras + 1 numero +
# 1 letra + 2 numeros. Ambas validas em circulacao.
_PLACA_ANTIGA_RE = re.compile(r"^[A-Z]{3}[0-9]{4}$")
_PLACA_MERCOSUL_RE = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")

# Chassi: padrao ISO 3779 -- 17 alfanumericos, sem I/O/Q (proibidos
# para evitar confusao com 1/0). Aceitamos chassis que possuem essas
# letras (alguns veiculos antigos brasileiros sao tolerantes), mas
# alertamos no schema.
_CHASSI_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_CHASSI_LOOSE_RE = re.compile(r"^[A-Z0-9]{17}$")


def normalize_placa(placa: str) -> str:
    """Normaliza placa: maiusculo, sem hifen/espaco/etc."""
    return _NON_ALNUM_RE.sub("", (placa or "").upper())


def is_valid_placa(placa: str) -> bool:
    """Aceita placa antiga (AAA0000) ou Mercosul (AAA0A00)."""
    p = normalize_placa(placa)
    return bool(_PLACA_ANTIGA_RE.match(p) or _PLACA_MERCOSUL_RE.match(p))


def is_placa_mercosul(placa: str) -> bool:
    """Util pra UI sinalizar 'placa Mercosul' versus padrao antigo."""
    return bool(_PLACA_MERCOSUL_RE.match(normalize_placa(placa)))


def normalize_renavam(renavam: str) -> str:
    """Renavam moderno tem 11 digitos. Versoes legadas tinham 9 -- a
    Detran faz padding com 0 a esquerda; aceitamos ambos e devolvemos
    sempre 11 digitos."""
    n = _DIGITS_RE.sub("", renavam or "")
    if 9 <= len(n) <= 11:
        return n.zfill(11)
    return n


def is_valid_renavam(renavam: str) -> bool:
    """Algoritmo do Detran: 10 primeiros digitos com pesos 3,2,9,8,
    7,6,5,4,3,2; soma * 10 % 11; se resultado for 10, DV = 0; senao
    DV = resultado.
    """
    n = normalize_renavam(renavam)
    if len(n) != 11 or not n.isdigit():
        return False
    if n == n[0] * 11:
        # Sequencia repetida (00000000000) passa no algoritmo mas nao
        # e renavam real.
        return False
    pesos = [3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma = sum(int(d) * p for d, p in zip(n[:10], pesos, strict=True))
    resto = (soma * 10) % 11
    dv = 0 if resto == 10 else resto
    return dv == int(n[10])


def normalize_chassi(chassi: str) -> str:
    return _NON_ALNUM_RE.sub("", (chassi or "").upper())


def is_valid_chassi(chassi: str, *, strict: bool = False) -> bool:
    """Valida chassi ISO 3779 (17 alfanumericos).

    `strict=True`: rejeita I/O/Q (caracteres proibidos pelo padrao).
    `strict=False` (default): aceita -- alguns veiculos antigos
    brasileiros tem chassi com essas letras e ainda estao em
    circulacao.
    """
    c = normalize_chassi(chassi)
    if strict:
        return bool(_CHASSI_RE.match(c))
    return bool(_CHASSI_LOOSE_RE.match(c))
