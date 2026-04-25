"""Pydantic schemas do modulo DP/SESMT (Modulo A)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.dp_sesmt.cpf import is_valid_cpf, normalize_cpf
from app.modules.dp_sesmt.models import STATUSES_VALIDOS


def _ensure_status_canonico(v: str | None) -> str | None:
    """Validador compartilhado entre EmployeeBase e EmployeeUpdate.

    Mantemos uma unica fonte da verdade (`STATUSES_VALIDOS` no models)
    em vez de repetir o set inline -- evita o caso onde EmployeeUpdate
    nao herdava de EmployeeBase e deixava passar status arbitrario,
    corrompendo filtros e badges da UI.
    """
    if v is None:
        return v
    if v not in STATUSES_VALIDOS:
        raise ValueError(
            "status deve ser um de: " + ", ".join(sorted(STATUSES_VALIDOS))
        )
    return v


class ModuleStatus(BaseModel):
    module: str
    implemented: bool
    stub: bool = False


# --- Employee CRUD -----------------------------------------------------------


class EmpregoAnteriorIn(BaseModel):
    empresa_cnpj: str | None = None
    empresa_razao_social: str | None = None
    cargo: str | None = None
    inicio: date | None = None
    fim: date | None = None
    motivo_desligamento: str | None = None
    observacoes: str | None = None


class EmpregoAnteriorOut(EmpregoAnteriorIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class EmployeeBase(BaseModel):
    cpf: str = Field(..., min_length=11, max_length=14)
    matricula: str | None = None
    nome_completo: str = Field(..., min_length=3, max_length=255)
    nome_social: str | None = None

    rg: str | None = None
    rg_orgao_emissor: str | None = None
    pis_pasep: str | None = None
    ctps_numero: str | None = None
    ctps_serie: str | None = None
    titulo_eleitor: str | None = None
    cnh_numero: str | None = None
    cnh_categoria: str | None = None
    cnh_validade: date | None = None

    data_nascimento: date | None = None
    sexo: str | None = None
    estado_civil: str | None = None
    escolaridade: str | None = None
    nome_mae: str | None = None
    nome_pai: str | None = None
    nacionalidade: str | None = "Brasileira"

    telefone: str | None = None
    email: str | None = None

    cep: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    cidade: str | None = None
    uf: str | None = None

    cargo: str = Field(..., min_length=2, max_length=100)
    obra: str | None = None
    setor: str | None = None
    tipo_contrato: str | None = None
    salario_base: Decimal | None = None
    data_admissao: date | None = None
    data_desligamento: date | None = None
    status: str | None = "ativo"

    aso_data: date | None = None
    aso_validade: date | None = None
    aso_resultado: str | None = None

    observacoes: str | None = None

    @field_validator("cpf")
    @classmethod
    def _validate_cpf(cls, v: str) -> str:
        norm = normalize_cpf(v)
        if not is_valid_cpf(norm):
            raise ValueError("CPF invalido (digitos verificadores nao batem)")
        return norm

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        return _ensure_status_canonico(v)


class EmployeeCreate(EmployeeBase):
    empregos_anteriores: list[EmpregoAnteriorIn] | None = None


class EmployeeUpdate(BaseModel):
    """Update parcial -- todos os campos opcionais.

    `cpf` deliberadamente excluido (chave natural; mudancas devem ser raras
    e exigem auditoria especial).
    """

    matricula: str | None = None
    nome_completo: str | None = None
    nome_social: str | None = None
    rg: str | None = None
    rg_orgao_emissor: str | None = None
    pis_pasep: str | None = None
    ctps_numero: str | None = None
    ctps_serie: str | None = None
    titulo_eleitor: str | None = None
    cnh_numero: str | None = None
    cnh_categoria: str | None = None
    cnh_validade: date | None = None

    data_nascimento: date | None = None
    sexo: str | None = None
    estado_civil: str | None = None
    escolaridade: str | None = None
    nome_mae: str | None = None
    nome_pai: str | None = None
    nacionalidade: str | None = None

    telefone: str | None = None
    email: str | None = None

    cep: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    cidade: str | None = None
    uf: str | None = None

    cargo: str | None = None
    obra: str | None = None
    setor: str | None = None
    tipo_contrato: str | None = None
    salario_base: Decimal | None = None
    data_admissao: date | None = None
    data_desligamento: date | None = None
    status: str | None = None

    aso_data: date | None = None
    aso_validade: date | None = None
    aso_resultado: str | None = None

    observacoes: str | None = None

    # Mesma validacao do EmployeeBase -- aqui precisa ser repetida porque
    # EmployeeUpdate herda de BaseModel diretamente (campos obrigatorios
    # do EmployeeBase como cpf/nome_completo/cargo nao se aplicam ao PUT
    # parcial, e Pydantic v2 nao tem `partial` nativo).
    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        return _ensure_status_canonico(v)


class EmployeeRead(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    created_at: datetime
    updated_at: datetime
    empregos_anteriores: list[EmpregoAnteriorOut] = []


# --- Dossie (consultas externas) --------------------------------------------


class CepLookupOut(BaseModel):
    cep: str
    logradouro: str | None = None
    bairro: str | None = None
    cidade: str | None = None
    uf: str | None = None
    complemento: str | None = None


class CnpjLookupOut(BaseModel):
    cnpj: str
    razao_social: str | None = None
    nome_fantasia: str | None = None
    situacao_cadastral: str | None = None
    cnae_principal: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    bairro: str | None = None
    cep: str | None = None
    uf: str | None = None
    municipio: str | None = None


class CpfLookupOut(BaseModel):
    cpf: str
    nome: str | None = None
    data_nascimento: str | None = None
    sexo: str | None = None
    situacao_cpf: str | None = None
    source: str  # "directdata" | "directdata_mock"


# --- Legacy stub (mantido para retrocompat com router antigo) ---------------


class EmployeeOnboardingRequest(BaseModel):
    cpf: str = Field(..., min_length=11, max_length=14)
    nome_completo: str
    cargo: str
    data_admissao: str
    obra_id: str | None = None
