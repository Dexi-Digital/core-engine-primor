"""Pydantic schemas da API do diagnostico."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.onedrive_diagnostico.service import (
    DiagnosticoResult,
    EntityReport,
    Finding,
)


class FindingSchema(BaseModel):
    kind: str
    area: str
    entity_key: str | None = None
    entity_label: str | None = None
    doc_tipo: str | None = None
    path: str | None = None
    message: str

    @classmethod
    def from_dataclass(cls, f: Finding) -> FindingSchema:
        return cls(
            kind=f.kind,
            area=f.area,
            entity_key=f.entity_key,
            entity_label=f.entity_label,
            doc_tipo=f.doc_tipo,
            path=f.path,
            message=f.message,
        )


class EntityReportSchema(BaseModel):
    area: str
    entity_key: str
    label: str
    ok: bool
    findings: list[FindingSchema]

    @classmethod
    def from_dataclass(cls, r: EntityReport) -> EntityReportSchema:
        return cls(
            area=r.area,
            entity_key=r.entity_key,
            label=r.label,
            ok=r.ok,
            findings=[FindingSchema.from_dataclass(f) for f in r.findings],
        )


class DiagnosticoResponse(BaseModel):
    root_folder: str
    total_files: int
    counts: dict[str, int] = Field(
        description=(
            "n de findings por tipo: faltando, extra, fora_do_padrao, "
            "entidade_fantasma, entidade_sem_pasta, ok"
        )
    )
    by_area: dict[str, list[EntityReportSchema]]

    @classmethod
    def from_result(cls, result: DiagnosticoResult) -> DiagnosticoResponse:
        return cls(
            root_folder=result.root_folder,
            total_files=result.total_files,
            counts=result.counts,
            by_area={
                area: [EntityReportSchema.from_dataclass(r) for r in reports]
                for area, reports in result.by_area.items()
            },
        )
