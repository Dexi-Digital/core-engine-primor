"""Coercao de valores monetarios para `Decimal`.

`coerce_valor` nasceu em `financeiro_contratos/service.py` e e reusado
aqui sem mudanca de semantica -- aquele modulo passou a importar deste.
`coerce_valor_rm` e a variante que entende as strings do TOTVS RM.

Regra do repo: dinheiro e `Decimal`, nunca `float`.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def coerce_valor(valor: Decimal | int | float | str | None) -> Decimal | None:
    """Normaliza `valor` para `Decimal`, evitando ruido binario de float.

    `Decimal(str(valor))` (nao `Decimal(valor)` direto) e o que garante que
    um float como `1234.1` vire `Decimal("1234.1")` exato, nao
    `Decimal("1234.099999999999909050529822707176208496093750")`.

    Um `valor` nao-numerico (ex.: payload malicioso ou bug do caller) faz
    `Decimal(str(...))` levantar `decimal.InvalidOperation` -- convertemos
    para `ValueError` para cair no mesmo tratamento 422 do resto do modulo
    (tipo/status invalidos), em vez de vazar como 500.
    """
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(f"valor invalido: {valor!r}") from exc


def coerce_valor_rm(valor: Decimal | int | float | str | None) -> Decimal | None:
    """Como `coerce_valor`, mas normaliza o separador decimal do RM antes.

    O RM devolve valor como string em varios endpoints e a formatacao
    depende da tag `WebServiceCulture` no Host: com `Invariant` vem
    ponto decimal ("1234.56"), sem ela vem a cultura pt-BR do servidor
    ("1.234,56"). `coerce_valor` sozinho levanta `InvalidOperation` no
    segundo caso -- dai esta funcao.

    Heuristica: o separador que aparece POR ULTIMO na string e o
    decimal; o outro e separador de milhar e e removido.

    AMBIGUIDADE conhecida: "1.234" (um unico ponto, tres casas) pode ser
    milhar pt-BR (1234) ou decimal invariant (1.234). Sem virgula na
    string, tratamos ponto como decimal -- que e o formato do
    `WebServiceCulture=Invariant` que pedimos ao time Cloud da TOTVS.
    Confirmar contra a instancia antes de ligar em producao.
    """
    if not isinstance(valor, str):
        return coerce_valor(valor)

    s = valor.strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None

    ultima_virgula = s.rfind(",")
    ultimo_ponto = s.rfind(".")
    if ultima_virgula > ultimo_ponto:
        # pt-BR: pontos sao milhar, virgula e decimal.
        s = s.replace(".", "").replace(",", ".")
    else:
        # Invariant (ou sem separador): virgulas seriam milhar.
        s = s.replace(",", "")

    return coerce_valor(s)
