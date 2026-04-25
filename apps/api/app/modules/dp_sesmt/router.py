"""Modulo A - Departamento Pessoal & SESMT.

Escopo (ver docs/roadmap.md):
    - Cadastro manual de funcionarios + dossie de admissao via APIs publicas.
    - Onboarding sync com Dominio/Onvio fica para um modulo separado.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.integrations.brasilapi.client import (
    BrasilAPIClient,
    BrasilAPIError,
    BrasilAPINotFoundError,
)
from app.integrations.directdata.client import DirectDataClient, DirectDataError
from app.integrations.viacep.client import (
    ViaCEPClient,
    ViaCEPError,
    ViaCEPNotFoundError,
)
from app.modules.dp_sesmt import service
from app.modules.dp_sesmt.schemas import (
    CepLookupOut,
    CnpjLookupOut,
    CpfLookupOut,
    EmployeeCreate,
    EmployeeOnboardingRequest,
    EmployeeRead,
    EmployeeUpdate,
    ModuleStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- legacy stubs (mantidos para nao quebrar testes/UI antigos) -------------


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="dp_sesmt", implemented=True)


@router.post("/onboarding", response_model=ModuleStatus)
async def onboarding(payload: EmployeeOnboardingRequest) -> ModuleStatus:
    _ = payload
    return ModuleStatus(module="dp_sesmt", implemented=True, stub=True)


# --- Employees CRUD ---------------------------------------------------------


@router.get("/employees", response_model=dict)
async def list_employees_endpoint(
    status: str | None = Query(None, max_length=16),
    obra: str | None = Query(None, max_length=128),
    search: str | None = Query(None, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows, total = await service.list_employees(
        db,
        status=status,
        obra=obra,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [EmployeeRead.model_validate(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/employees", response_model=EmployeeRead, status_code=201)
async def create_employee_endpoint(
    payload: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
) -> EmployeeRead:
    existing = await service.get_employee_by_cpf(db, payload.cpf)
    if existing is not None:
        raise HTTPException(409, f"Ja existe funcionario com CPF {payload.cpf}")
    data = payload.model_dump(exclude={"empregos_anteriores"})
    employee = await service.create_employee(
        db,
        empregos_anteriores=payload.empregos_anteriores,
        **data,
    )
    return EmployeeRead.model_validate(employee)


@router.get("/employees/{employee_id}", response_model=EmployeeRead)
async def get_employee_endpoint(
    employee_id: int, db: AsyncSession = Depends(get_db)
) -> EmployeeRead:
    employee = await service.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(404, f"Funcionario {employee_id} nao encontrado")
    return EmployeeRead.model_validate(employee)


@router.put("/employees/{employee_id}", response_model=EmployeeRead)
async def update_employee_endpoint(
    employee_id: int,
    payload: EmployeeUpdate,
    db: AsyncSession = Depends(get_db),
) -> EmployeeRead:
    fields = payload.model_dump(exclude_unset=True)
    employee = await service.update_employee(db, employee_id, **fields)
    if employee is None:
        raise HTTPException(404, f"Funcionario {employee_id} nao encontrado")
    return EmployeeRead.model_validate(employee)


@router.delete("/employees/{employee_id}", status_code=204)
async def delete_employee_endpoint(
    employee_id: int, db: AsyncSession = Depends(get_db)
) -> None:
    deleted = await service.delete_employee(db, employee_id)
    if not deleted:
        raise HTTPException(404, f"Funcionario {employee_id} nao encontrado")


# --- Dossie de admissao -----------------------------------------------------


def _get_viacep() -> ViaCEPClient:
    return ViaCEPClient()


def _get_brasilapi() -> BrasilAPIClient:
    return BrasilAPIClient()


def _get_directdata() -> DirectDataClient:
    settings = get_settings()
    return DirectDataClient(api_key=settings.directdata_api_key)


@router.get("/dossie/cep/{cep}", response_model=CepLookupOut)
async def dossie_cep(
    cep: str,
    employee_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    viacep: ViaCEPClient = Depends(_get_viacep),
) -> CepLookupOut:
    try:
        result = await service.lookup_cep(
            db, cep, viacep=viacep, employee_id=employee_id
        )
    except ViaCEPNotFoundError as exc:
        raise HTTPException(404, f"CEP {cep} nao encontrado") from exc
    except (ViaCEPError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await viacep.aclose()
    return CepLookupOut.model_validate(result)


@router.get("/dossie/cnpj/{cnpj}", response_model=CnpjLookupOut)
async def dossie_cnpj(
    cnpj: str,
    employee_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    brasilapi: BrasilAPIClient = Depends(_get_brasilapi),
) -> CnpjLookupOut:
    try:
        result = await service.lookup_cnpj(
            db, cnpj, brasilapi=brasilapi, employee_id=employee_id
        )
    except BrasilAPINotFoundError as exc:
        raise HTTPException(404, f"CNPJ {cnpj} nao encontrado") from exc
    except (BrasilAPIError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await brasilapi.aclose()
    return CnpjLookupOut.model_validate(result)


@router.get("/dossie/cpf/{cpf}", response_model=CpfLookupOut)
async def dossie_cpf(
    cpf: str,
    employee_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    directdata: DirectDataClient = Depends(_get_directdata),
) -> CpfLookupOut:
    try:
        result = await service.lookup_cpf(
            db, cpf, directdata=directdata, employee_id=employee_id
        )
    except (DirectDataError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await directdata.aclose()
    return CpfLookupOut.model_validate(result)
