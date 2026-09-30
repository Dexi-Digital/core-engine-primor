"""Relatorio de % de atendimento da varredura documental (demanda #1).

Calculado sob demanda a partir dos findings de um run -- nao persiste
nada novo (evita migration e mantem runs antigos consultaveis com a
mesma regra).

**Regra de atendimento** (documentada aqui e repetida na tela/CSV):

- `ok`       -> ATENDE.
- `vencendo` -> ATENDE, mas em alerta: o documento existe e ainda vale
  hoje; so vai deixar de valer em <= 30 dias. Conta no numerador para
  nao punir quem esta em dia e aparece separado em `vencendo` para o
  RH agir antes.
- `vencido`  -> NAO ATENDE (documento existe mas nao vale mais).
- `ausente`  -> NAO ATENDE.

    pct_atendimento = (ok + vencendo) / total_exigido * 100
    pct_em_dia      = ok / total_exigido * 100   (sem nada em alerta)

`total_exigido` = numero de findings, ou seja, documentos obrigatorios
apos aplicar as regras condicionais do checklist (ex.: toxicologico so
para motorista). Quando `total_exigido == 0` o percentual e `None`
(nao ha o que atender -- mostrar 100% esconderia uma entidade sem
checklist).

Niveis de agregacao:

- `geral`: o run inteiro.
- `por_area`: area = perfil do checklist (dp, sst, frota, empresa,
  obra). Um mesmo funcionario aparece em dp e sst.
- `por_tipo_entidade`: employee / veiculo / obra / empresa.
- `por_entidade`: cada funcionario/veiculo/obra/empresa (somando todas
  as areas em que aparece), ordenado do menor atendimento para o maior
  -- quem precisa de acao primeiro vem no topo.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.modules.diagnostico.models import (
    FINDING_AUSENTE,
    FINDING_OK,
    FINDING_VENCENDO,
    FINDING_VENCIDO,
)

CRITERIO_ATENDIMENTO = (
    "pct_atendimento = (ok + vencendo) / total exigido. "
    "'vencendo' atende mas esta em alerta (vence em ate 30 dias); "
    "'vencido' e 'ausente' nao atendem. "
    "pct_em_dia = ok / total exigido."
)


class _FindingLike(Protocol):
    area: str
    entity_type: str
    entity_id: int | None
    entity_label: str
    status: str


@dataclass(slots=True)
class Contagem:
    total: int = 0
    ok: int = 0
    vencendo: int = 0
    vencido: int = 0
    ausente: int = 0

    def add(self, status: str) -> None:
        self.total += 1
        if status == FINDING_OK:
            self.ok += 1
        elif status == FINDING_VENCENDO:
            self.vencendo += 1
        elif status == FINDING_VENCIDO:
            self.vencido += 1
        elif status == FINDING_AUSENTE:
            self.ausente += 1
        # status desconhecido: conta no total e nao atende (conservador).

    @property
    def atendidos(self) -> int:
        return self.ok + self.vencendo

    @property
    def pendentes(self) -> int:
        return self.total - self.atendidos

    @property
    def pct_atendimento(self) -> float | None:
        if self.total == 0:
            return None
        return round(self.atendidos * 100 / self.total, 1)

    @property
    def pct_em_dia(self) -> float | None:
        if self.total == 0:
            return None
        return round(self.ok * 100 / self.total, 1)

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "total": self.total,
            "ok": self.ok,
            "vencendo": self.vencendo,
            "vencido": self.vencido,
            "ausente": self.ausente,
            "atendidos": self.atendidos,
            "pendentes": self.pendentes,
            "pct_atendimento": self.pct_atendimento,
            "pct_em_dia": self.pct_em_dia,
        }


@dataclass(slots=True)
class _EntidadeAcc:
    entity_type: str
    entity_id: int | None
    entity_label: str
    areas: set[str] = field(default_factory=set)
    contagem: Contagem = field(default_factory=Contagem)


def entity_key(f: _FindingLike) -> tuple[str, str]:
    # Empresa nao tem entity_id (a chave e o CNPJ em entity_label).
    ident = str(f.entity_id) if f.entity_id is not None else f.entity_label
    return (f.entity_type, ident)


def compute_atendimento(findings: Iterable[_FindingLike]) -> dict:
    """Agrega findings no relatorio de atendimento (ver docstring do modulo)."""
    geral = Contagem()
    por_area: dict[str, Contagem] = {}
    por_tipo: dict[str, Contagem] = {}
    por_entidade: dict[tuple[str, str], _EntidadeAcc] = {}

    for f in findings:
        geral.add(f.status)
        por_area.setdefault(f.area, Contagem()).add(f.status)
        por_tipo.setdefault(f.entity_type, Contagem()).add(f.status)
        key = entity_key(f)
        acc = por_entidade.get(key)
        if acc is None:
            acc = _EntidadeAcc(
                entity_type=f.entity_type,
                entity_id=f.entity_id,
                entity_label=f.entity_label,
            )
            por_entidade[key] = acc
        acc.areas.add(f.area)
        acc.contagem.add(f.status)

    entidades = sorted(
        por_entidade.values(),
        key=lambda a: (
            a.contagem.pct_atendimento
            if a.contagem.pct_atendimento is not None
            else 101.0,
            a.entity_type,
            a.entity_label,
        ),
    )

    return {
        "criterio": CRITERIO_ATENDIMENTO,
        "geral": geral.as_dict(),
        "por_area": [
            {"area": area, **c.as_dict()}
            for area, c in sorted(por_area.items())
        ],
        "por_tipo_entidade": [
            {"entity_type": t, **c.as_dict()}
            for t, c in sorted(por_tipo.items())
        ],
        "por_entidade": [
            {
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "entity_label": a.entity_label,
                "areas": sorted(a.areas),
                **a.contagem.as_dict(),
            }
            for a in entidades
        ],
    }


def pct_por_entidade(
    findings: Iterable[_FindingLike],
) -> dict[tuple[str, str], float | None]:
    """Mapa (entity_type, id-ou-label) -> pct_atendimento. Usado no CSV."""
    acc: dict[tuple[str, str], Contagem] = {}
    for f in findings:
        acc.setdefault(entity_key(f), Contagem()).add(f.status)
    return {k: c.pct_atendimento for k, c in acc.items()}


__all__ = [
    "CRITERIO_ATENDIMENTO",
    "Contagem",
    "compute_atendimento",
    "entity_key",
    "pct_por_entidade",
]
