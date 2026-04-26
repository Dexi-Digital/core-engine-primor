"""Configuracao do checklist de Diagnostico Documental (D1).

Cada area (DP, SST, Frota, Empresa, Obra) tem uma lista de
`DocumentRequirement` que diz:
- qual o tipo do doc esperado
- qual o label legivel pra UI
- de onde puxar os dados (qual tabela/coluna ou query)
- se tem regra condicional (ex.: NR-12 so se `is_operador_maquina`)

O runner percorre cada entidade da area e avalia cada requirement
contra o estado atual do banco. O resultado e gravado como
`DiagnosticoFinding`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.modules.dp_sesmt.models import (
    DOC_EMP_ACORDO_COMPENSACAO,
    DOC_EMP_CONTRATO_EXPERIENCIA,
    DOC_EMP_DECL_FAMILIA,
    DOC_EMP_FICHA_EPI,
    DOC_EMP_FICHA_SALARIO_FAMILIA,
    DOC_EMP_LABELS,
    DOC_EMP_LISTA_INTEGRACAO,
    DOC_EMP_NR10,
    DOC_EMP_NR12,
    DOC_EMP_NR18,
    DOC_EMP_NR35,
    DOC_EMP_OS,
    DOC_EMP_TERMO_LGPD,
    DOC_EMP_TERMO_RESPONSABILIDADE,
    DOC_EMP_TERMO_VT,
    DOC_EMP_TOXICOLOGICO,
    Employee,
)
from app.modules.licitacoes.models import (
    DOC_EMPRESA_ALTERACAO,
    DOC_EMPRESA_BALANCO,
    DOC_EMPRESA_CAGEF,
    DOC_EMPRESA_CONTRATO_SOCIAL,
    DOC_EMPRESA_LABELS,
    DOC_EMPRESA_SICAF,
    DOC_EMPRESA_SUCAF,
)
from app.modules.manutencao_frota.models import (
    DOC_CRLV,
    DOC_DPVAT,
    DOC_IPVA,
    DOC_LICENCIAMENTO,
    DOC_SEGURO,
)
from app.modules.obras.models import (
    DOC_ALVARA,
    DOC_ART,
    DOC_CIPA_OBRA,
    DOC_DIARIO_OBRA,
    DOC_PCMAT,
)
from app.modules.obras.models import (
    DOC_LABELS as DOC_OBRA_LABELS,
)

# Constantes de tipos de CND (espelham `TIPOS_CERTIDAO` em
# app.modules.licitacoes.certidoes -- string livre no banco, valida no
# codigo). Centralizadas aqui para manter o checklist coeso.
CERT_FEDERAL = "CND_FEDERAL"
CERT_FGTS = "FGTS"
CERT_CNDT = "CNDT"
CERT_INSS = "INSS"
CERT_ESTADUAL = "ESTADUAL"
CERT_MUNICIPAL = "MUNICIPAL"


@dataclass(slots=True)
class DocumentRequirement:
    """Um item do checklist."""

    area: str
    doc_tipo: str
    doc_label: str
    # Predicado opcional aplicado a entidade -- se False, requirement e
    # ignorado para essa entidade. Usado para regras condicionais (NR-12
    # so se is_operador_maquina). Recebe a entidade ORM como argumento.
    aplicavel_se: Callable[[object], bool] | None = field(default=None)


# --- DP (per-funcionario) -------------------------------------------------
# Documentos basicos exigidos para todo funcionario CLT. ASO e validado
# separadamente lendo o campo `aso_validade` em Employee (compat com A.2).
CHECKLIST_DP_FUNCIONARIO: list[DocumentRequirement] = [
    DocumentRequirement(
        area="dp",
        doc_tipo="ASO",
        doc_label="ASO (Atestado de Saude Ocupacional)",
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo="CTPS",
        doc_label="CTPS (Carteira de Trabalho)",
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo="CONTRATO_TRABALHO",
        doc_label="Contrato de trabalho",
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo="FICHA_REGISTRO",
        doc_label="Ficha de registro",
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_TERMO_LGPD,
        doc_label=DOC_EMP_LABELS[DOC_EMP_TERMO_LGPD],
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_TERMO_VT,
        doc_label=DOC_EMP_LABELS[DOC_EMP_TERMO_VT],
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_TERMO_RESPONSABILIDADE,
        doc_label=DOC_EMP_LABELS[DOC_EMP_TERMO_RESPONSABILIDADE],
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_DECL_FAMILIA,
        doc_label=DOC_EMP_LABELS[DOC_EMP_DECL_FAMILIA],
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_FICHA_SALARIO_FAMILIA,
        doc_label=DOC_EMP_LABELS[DOC_EMP_FICHA_SALARIO_FAMILIA],
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_ACORDO_COMPENSACAO,
        doc_label=DOC_EMP_LABELS[DOC_EMP_ACORDO_COMPENSACAO],
    ),
    DocumentRequirement(
        area="dp",
        doc_tipo=DOC_EMP_CONTRATO_EXPERIENCIA,
        doc_label=DOC_EMP_LABELS[DOC_EMP_CONTRATO_EXPERIENCIA],
    ),
]


# --- SST (per-funcionario) ------------------------------------------------
# Construcao civil -- todo funcionario tem NR-18, OS, lista de
# integracao, ficha de EPI. Demais NRs sao condicionais via flags.
def _is_construcao(emp: object) -> bool:
    """NR-18 obrigatoria para todos exceto admin office."""
    return not getattr(emp, "is_admin_office", False)


def _is_motorista(emp: object) -> bool:
    return bool(getattr(emp, "is_motorista", False))


def _is_operador(emp: object) -> bool:
    return bool(getattr(emp, "is_operador_maquina", False))


def _is_alturas(emp: object) -> bool:
    return bool(getattr(emp, "is_alturas", False))


def _is_eletricista(emp: object) -> bool:
    return bool(getattr(emp, "is_eletricista", False))


CHECKLIST_SST_FUNCIONARIO: list[DocumentRequirement] = [
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_OS,
        doc_label=DOC_EMP_LABELS[DOC_EMP_OS],
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_LISTA_INTEGRACAO,
        doc_label=DOC_EMP_LABELS[DOC_EMP_LISTA_INTEGRACAO],
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_FICHA_EPI,
        doc_label=DOC_EMP_LABELS[DOC_EMP_FICHA_EPI],
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_NR18,
        doc_label=DOC_EMP_LABELS[DOC_EMP_NR18],
        aplicavel_se=_is_construcao,
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_NR12,
        doc_label=DOC_EMP_LABELS[DOC_EMP_NR12],
        aplicavel_se=_is_operador,
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_NR35,
        doc_label=DOC_EMP_LABELS[DOC_EMP_NR35],
        aplicavel_se=_is_alturas,
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_NR10,
        doc_label=DOC_EMP_LABELS[DOC_EMP_NR10],
        aplicavel_se=_is_eletricista,
    ),
    DocumentRequirement(
        area="sst",
        doc_tipo=DOC_EMP_TOXICOLOGICO,
        doc_label=DOC_EMP_LABELS[DOC_EMP_TOXICOLOGICO],
        aplicavel_se=_is_motorista,
    ),
]


# --- Frota (per-veiculo) -------------------------------------------------
# Documentos basicos exigidos para todo veiculo. AET/CIV/CTPP entrariam
# como condicionais quando o tipo do veiculo for "prancha"/"comboio";
# por ora ficam fora pois nao temos flag boolean em frota_veiculos.
CHECKLIST_FROTA_VEICULO: list[DocumentRequirement] = [
    DocumentRequirement(
        area="frota", doc_tipo=DOC_CRLV, doc_label="CRLV"
    ),
    DocumentRequirement(
        area="frota", doc_tipo=DOC_IPVA, doc_label="IPVA"
    ),
    DocumentRequirement(
        area="frota", doc_tipo=DOC_LICENCIAMENTO, doc_label="Licenciamento"
    ),
    DocumentRequirement(
        area="frota", doc_tipo=DOC_SEGURO, doc_label="Seguro"
    ),
    DocumentRequirement(
        area="frota", doc_tipo=DOC_DPVAT, doc_label="DPVAT"
    ),
]


# --- Empresa (per-CNPJ) --------------------------------------------------
# CNDs continuam em `certidoes_empresa` (com alertas D.6 ja em prod).
# Aqui agregamos: certidoes habilitatorias + cadastros oficiais.
CHECKLIST_EMPRESA: list[DocumentRequirement] = [
    DocumentRequirement(
        area="empresa", doc_tipo=CERT_FEDERAL, doc_label="CND Federal"
    ),
    DocumentRequirement(
        area="empresa", doc_tipo=CERT_ESTADUAL, doc_label="CND Estadual"
    ),
    DocumentRequirement(
        area="empresa", doc_tipo=CERT_MUNICIPAL, doc_label="CND Municipal"
    ),
    DocumentRequirement(
        area="empresa", doc_tipo=CERT_FGTS, doc_label="CRF FGTS"
    ),
    DocumentRequirement(
        area="empresa", doc_tipo=CERT_CNDT, doc_label="CNDT"
    ),
    DocumentRequirement(
        area="empresa", doc_tipo=CERT_INSS, doc_label="CND INSS"
    ),
    DocumentRequirement(
        area="empresa",
        doc_tipo=DOC_EMPRESA_CONTRATO_SOCIAL,
        doc_label=DOC_EMPRESA_LABELS[DOC_EMPRESA_CONTRATO_SOCIAL],
    ),
    DocumentRequirement(
        area="empresa",
        doc_tipo=DOC_EMPRESA_ALTERACAO,
        doc_label=DOC_EMPRESA_LABELS[DOC_EMPRESA_ALTERACAO],
    ),
    DocumentRequirement(
        area="empresa",
        doc_tipo=DOC_EMPRESA_BALANCO,
        doc_label=DOC_EMPRESA_LABELS[DOC_EMPRESA_BALANCO],
    ),
    DocumentRequirement(
        area="empresa",
        doc_tipo=DOC_EMPRESA_SICAF,
        doc_label=DOC_EMPRESA_LABELS[DOC_EMPRESA_SICAF],
    ),
    DocumentRequirement(
        area="empresa",
        doc_tipo=DOC_EMPRESA_CAGEF,
        doc_label=DOC_EMPRESA_LABELS[DOC_EMPRESA_CAGEF],
    ),
    DocumentRequirement(
        area="empresa",
        doc_tipo=DOC_EMPRESA_SUCAF,
        doc_label=DOC_EMPRESA_LABELS[DOC_EMPRESA_SUCAF],
    ),
]


# --- Obra (per-canteiro) -------------------------------------------------
CHECKLIST_OBRA: list[DocumentRequirement] = [
    DocumentRequirement(
        area="obra", doc_tipo=DOC_ART, doc_label=DOC_OBRA_LABELS[DOC_ART]
    ),
    DocumentRequirement(
        area="obra", doc_tipo=DOC_ALVARA, doc_label=DOC_OBRA_LABELS[DOC_ALVARA]
    ),
    DocumentRequirement(
        area="obra", doc_tipo=DOC_PCMAT, doc_label=DOC_OBRA_LABELS[DOC_PCMAT]
    ),
    DocumentRequirement(
        area="obra",
        doc_tipo=DOC_CIPA_OBRA,
        doc_label=DOC_OBRA_LABELS[DOC_CIPA_OBRA],
    ),
    DocumentRequirement(
        area="obra",
        doc_tipo=DOC_DIARIO_OBRA,
        doc_label=DOC_OBRA_LABELS[DOC_DIARIO_OBRA],
    ),
]


def applicable_dp_requirements(
    emp: Employee,
) -> list[DocumentRequirement]:
    """Retorna apenas os requirements DP+SST aplicaveis a esse funcionario."""
    out: list[DocumentRequirement] = []
    for req in (*CHECKLIST_DP_FUNCIONARIO, *CHECKLIST_SST_FUNCIONARIO):
        if req.aplicavel_se is None or req.aplicavel_se(emp):
            out.append(req)
    return out
