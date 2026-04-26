"""Parser de paths do OneDrive para `ParsedMatch`.

Convencao de pastas (sob `ms_graph_root_folder`, default
`MotorCentral/editais` -- pode ser redefinido em env):

    dp/<employee_id>/<DOC_TIPO>.<ext>
    frota/<PLACA>/<DOC_TIPO>.<ext>
    obras/<OBRA_CODIGO>/<DOC_TIPO>.<ext>
    empresa/<DOC_TIPO>.<ext>

Exemplo: `dp/42/NR12.pdf` -> `ParsedMatch(area='dp', entity_key='42',
doc_tipo='NR12', filename='NR12.pdf')`.

O parser so valida que o `area` e valido e que o path casa com o
shape esperado. Validacoes mais fortes (placa existe na frota,
employee_id vira int valido, doc_tipo esta em `DOC_*_TIPOS_VALIDOS`)
acontecem no service -- queremos retornar erros com contexto util
em `summary_json["errors"]` em vez de jogar fora o item silenciosamente.
"""
from __future__ import annotations

from dataclasses import dataclass

# Mapeamento de primeiro segmento do path -> area canonica do sync.
# Aceita minusculas E maiusculas (tolerancia a usuarios finais
# que podem subir pastas com qualquer casing).
AREA_DP = "dp"
AREA_FROTA = "frota"
AREA_OBRAS = "obras"
AREA_EMPRESA = "empresa"
AREAS_VALIDAS = frozenset({AREA_DP, AREA_FROTA, AREA_OBRAS, AREA_EMPRESA})


@dataclass(slots=True, frozen=True)
class ParsedMatch:
    area: str
    entity_key: str | None  # None para `empresa` (singleton)
    doc_tipo: str
    filename: str


def parse_path(relative_path: str) -> ParsedMatch | None:
    """Parseia `relative_path` (relativo ao `root_folder`) em `ParsedMatch`.

    Retorna `None` se o path nao casa com a convencao -- o caller trata
    como skip. Erros de validacao (ex.: `dp/abc/NR12.pdf`) tambem
    retornam None; o caller registra em `errors` se quiser.
    """
    parts = [p for p in relative_path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    area = parts[0].lower()
    if area not in AREAS_VALIDAS:
        return None
    if area == AREA_EMPRESA:
        # empresa/<DOC_TIPO>.<ext>
        if len(parts) != 2:
            return None
        filename = parts[1]
        doc_tipo = _doc_tipo_from_filename(filename)
        if doc_tipo is None:
            return None
        return ParsedMatch(
            area=area,
            entity_key=None,
            doc_tipo=doc_tipo,
            filename=filename,
        )
    # dp/<id>/<DOC_TIPO>.<ext>, frota/<placa>/<tipo>.<ext>,
    # obras/<codigo>/<tipo>.<ext>
    if len(parts) != 3:
        return None
    entity_key = parts[1]
    filename = parts[2]
    doc_tipo = _doc_tipo_from_filename(filename)
    if doc_tipo is None or not entity_key:
        return None
    return ParsedMatch(
        area=area,
        entity_key=entity_key,
        doc_tipo=doc_tipo,
        filename=filename,
    )


def _doc_tipo_from_filename(filename: str) -> str | None:
    """Extrai o doc_tipo do filename (stem upper, sem extensao).

    `NR12.pdf` -> `NR12`, `Contrato Social.pdf` -> `CONTRATO_SOCIAL`.
    Espacos viram `_`; aceita letras, digitos e `_`.
    """
    if "." not in filename:
        return None
    stem = filename.rsplit(".", 1)[0].strip()
    if not stem:
        return None
    # Normaliza: espacos -> _ , upper.
    normalized = "_".join(stem.upper().split())
    return normalized or None
