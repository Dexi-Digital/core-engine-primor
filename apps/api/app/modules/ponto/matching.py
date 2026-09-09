"""Ligacao entre local de trabalho do Solides e obra do Motor Central.

O ADR-003 deixou em aberto como estabelecer o vinculo funcionario ->
obra ("workplace vs geolocalizacao, aguardando Solides"). A resposta
veio da API real em 27/08/2026: geolocalizacao NAO existe em nenhum
modelo, e os 83 locais de trabalho da conta da Primor SAO as obras --
"Obra 243", "Obra 217 - Januaria", "OBRA 246 - ABAETE" -- ao lado de
alguns administrativos ("ADM PRIMOR", "ESCRITORIO PRIMOR").

Entao o vinculo e por local de trabalho, e a ponte e o codigo da obra
embutido no nome. Local administrativo simplesmente nao tem obra: isso
e o comportamento correto, nao uma falha de match.
"""
from __future__ import annotations

import re
import unicodedata

# "Obra 243", "OBRA 246 - ABAETE", "Obra c008 Santa Margarida",
# "Obra 021 CTC" -- prefixo "obra" seguido do codigo, que pode ter uma
# letra na frente. O resto do nome e a localidade e nao entra na chave.
_PADRAO_OBRA = re.compile(r"^\s*obra\s+([a-z]?\d+)\b", re.IGNORECASE)


def normalizar_nome_local(nome: str | None) -> str:
    """Forma canonica do nome: sem acento, sem caixa, espacos colapsados.

    Serve para deduplicar. Na conta real da Primor, "Obra 010 CTC"
    aparece DUAS vezes na lista de locais -- sem normalizar, a
    duplicidade passa despercebida e vira dois vinculos para a mesma
    obra.
    """
    if not nome:
        return ""
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFD", nome)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.lower().split())


def codigo_obra_de_local(nome: str | None) -> str | None:
    """Codigo da obra embutido no nome do local, ou None.

    Devolve o codigo em CAIXA ALTA ("c008" -> "C008") porque
    `obras_obra.codigo` e a chave de match e precisa de forma unica.
    None para locais administrativos, consorcios e nomes sem o prefixo
    "Obra" -- nesses casos o local existe sem vinculo com obra.
    """
    if not nome:
        return None
    achado = _PADRAO_OBRA.match(nome)
    if not achado:
        return None
    return achado.group(1).upper()
