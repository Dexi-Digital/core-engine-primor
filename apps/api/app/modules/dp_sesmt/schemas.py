from __future__ import annotations

from pydantic import BaseModel, Field


class ModuleStatus(BaseModel):
    module: str
    implemented: bool
    stub: bool = False


class EmployeeOnboardingRequest(BaseModel):
    cpf: str = Field(..., min_length=11, max_length=14)
    nome_completo: str
    cargo: str
    data_admissao: str  # ISO-8601
    obra_id: str | None = None
