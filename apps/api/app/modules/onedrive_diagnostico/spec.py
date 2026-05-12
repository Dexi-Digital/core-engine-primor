"""Especificacao dos docs obrigatorios por area.

V1: hardcoded. Evoluir pra configuravel via UI/YAML quando houver mais
de um cliente/tenant.

Cada `AreaSpec` lista:
  - required:  tipos que TODA entidade da area deve ter no OneDrive
  - optional:  tipos reconhecidos mas nao obrigatorios (nao gera
               "faltando", mas tambem nao gera "extra")

Os conjuntos de `required ∪ optional` derivam das constantes
`DOC_*_TIPOS_VALIDOS` nos modulos existentes -- se um arquivo aparecer
com um doc_tipo fora desse conjunto, vira "extra".

Notas sobre o subset "required":
  - DP: CTPS/CONTRATO_TRABALHO/FICHA_REGISTRO sao o minimo regulatorio
        pra qualquer funcionario CLT (OS/EPI/LGPD sao comuns mas
        dependem da funcao, deixamos optional)
  - Frota: crlv/seguro sao obrigatorios em todo veiculo em circulacao;
           IPVA tambem, mas varia por UF isento
  - Obras: ART/ALVARA sao minimo pra obra ativa; o resto varia pelo
           porte/natureza da obra
  - Empresa: CONTRATO_SOCIAL e o unico item societario realmente
             obrigatorio sempre; o resto e optional
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.dp_sesmt.models import (
    DOC_EMP_CONTRATO_TRABALHO,
    DOC_EMP_CTPS,
    DOC_EMP_FICHA_REGISTRO,
    DOC_EMP_TIPOS_VALIDOS,
)
from app.modules.licitacoes.models import (
    DOC_EMPRESA_CONTRATO_SOCIAL,
    DOC_EMPRESA_TIPOS_VALIDOS,
)
from app.modules.manutencao_frota.models import (
    DOC_CRLV,
    DOC_IPVA,
    DOC_SEGURO,
)
from app.modules.manutencao_frota.models import (
    TIPOS_DOC_VALIDOS as DOC_FROTA_TIPOS_VALIDOS,
)
from app.modules.obras.models import (
    DOC_ALVARA,
    DOC_ART,
)
from app.modules.obras.models import (
    DOC_TIPOS_VALIDOS as DOC_OBRA_TIPOS_VALIDOS,
)

AREA_DP = "dp"
AREA_FROTA = "frota"
AREA_OBRAS = "obras"
AREA_EMPRESA = "empresa"

AREAS: tuple[str, ...] = (AREA_DP, AREA_FROTA, AREA_OBRAS, AREA_EMPRESA)


@dataclass(frozen=True, slots=True)
class AreaSpec:
    area: str
    required: frozenset[str]
    optional: frozenset[str]

    @property
    def known(self) -> frozenset[str]:
        return self.required | self.optional


SPEC: dict[str, AreaSpec] = {
    AREA_DP: AreaSpec(
        area=AREA_DP,
        required=frozenset(
            {
                DOC_EMP_CTPS,
                DOC_EMP_CONTRATO_TRABALHO,
                DOC_EMP_FICHA_REGISTRO,
            }
        ),
        optional=DOC_EMP_TIPOS_VALIDOS
        - {DOC_EMP_CTPS, DOC_EMP_CONTRATO_TRABALHO, DOC_EMP_FICHA_REGISTRO},
    ),
    AREA_FROTA: AreaSpec(
        area=AREA_FROTA,
        required=frozenset({DOC_CRLV, DOC_SEGURO, DOC_IPVA}),
        optional=DOC_FROTA_TIPOS_VALIDOS
        - {DOC_CRLV, DOC_SEGURO, DOC_IPVA},
    ),
    AREA_OBRAS: AreaSpec(
        area=AREA_OBRAS,
        required=frozenset({DOC_ART, DOC_ALVARA}),
        optional=DOC_OBRA_TIPOS_VALIDOS - {DOC_ART, DOC_ALVARA},
    ),
    AREA_EMPRESA: AreaSpec(
        area=AREA_EMPRESA,
        required=frozenset({DOC_EMPRESA_CONTRATO_SOCIAL}),
        optional=DOC_EMPRESA_TIPOS_VALIDOS
        - {DOC_EMPRESA_CONTRATO_SOCIAL},
    ),
}


def get_spec(area: str) -> AreaSpec:
    try:
        return SPEC[area]
    except KeyError as exc:
        raise ValueError(
            f"area invalida {area!r}; validas: {sorted(SPEC)}"
        ) from exc
