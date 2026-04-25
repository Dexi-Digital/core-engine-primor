"""Modulo A -- DP/SESMT business logic.

Inicialmente: CRUD de funcionarios + helpers do dossie de admissao
(consultas a APIs publicas: ViaCEP, BrasilAPI). DirectData entra como
mock ate a Primor contratar o plano.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.modules.dp_sesmt.cpf import is_valid_cpf, normalize_cpf
from app.modules.dp_sesmt.models import (
    DossieConsultaLog,
    Employee,
    EmpregoAnterior,
)

logger = logging.getLogger(__name__)


# --- legacy stub (mantido para nao quebrar router antigo) -------------------


async def start_onboarding(cpf: str) -> str:
    _ = cpf
    return "corr-stub"


# --- Employee CRUD ----------------------------------------------------------


async def create_employee(
    db: AsyncSession,
    *,
    empregos_anteriores: Sequence[Any] | None = None,
    **fields: Any,
) -> Employee:
    if "cpf" in fields:
        fields["cpf"] = normalize_cpf(fields["cpf"])
    employee = Employee(**fields)
    if empregos_anteriores:
        for emp in empregos_anteriores:
            data = emp.model_dump() if hasattr(emp, "model_dump") else dict(emp)
            employee.empregos_anteriores.append(EmpregoAnterior(**data))
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    # Recarrega com a coleção de empregos para evitar lazy-load no return.
    return await get_employee(db, employee.id) or employee


async def get_employee(db: AsyncSession, employee_id: int) -> Employee | None:
    stmt = (
        select(Employee)
        .where(Employee.id == employee_id)
        .options(selectinload(Employee.empregos_anteriores))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_employee_by_cpf(
    db: AsyncSession, cpf: str
) -> Employee | None:
    cpf_norm = normalize_cpf(cpf)
    stmt = (
        select(Employee)
        .where(Employee.cpf == cpf_norm)
        .options(selectinload(Employee.empregos_anteriores))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_employees(
    db: AsyncSession,
    *,
    status: str | None = None,
    obra: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Employee], int]:
    """Lista funcionarios com filtros + paginacao.

    Retorna (rows, total_count) -- count nao filtrado por limit/offset
    para alimentar paginacao na UI.
    """
    base_filters = []
    if status:
        base_filters.append(Employee.status == status)
    if obra:
        base_filters.append(Employee.obra == obra)
    if search:
        like = f"%{search.strip()}%"
        # CPF stored sem mascara -- normalizamos a busca tambem.
        cpf_search = normalize_cpf(search)
        conditions = [
            Employee.nome_completo.ilike(like),
            Employee.matricula.ilike(like),
            Employee.cargo.ilike(like),
        ]
        if cpf_search:
            conditions.append(Employee.cpf.ilike(f"%{cpf_search}%"))
        base_filters.append(or_(*conditions))

    from sqlalchemy import func

    count_stmt = select(func.count(Employee.id))
    list_stmt = (
        select(Employee)
        .options(selectinload(Employee.empregos_anteriores))
        .order_by(Employee.nome_completo)
        .limit(limit)
        .offset(offset)
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = list((await db.execute(list_stmt)).scalars().all())
    return rows, int(total)


async def update_employee(
    db: AsyncSession, employee_id: int, **fields: Any
) -> Employee | None:
    row = await db.get(Employee, employee_id)
    if row is None:
        return None
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    await db.commit()
    return await get_employee(db, employee_id)


async def delete_employee(db: AsyncSession, employee_id: int) -> bool:
    row = await db.get(Employee, employee_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


# --- Dossie helpers ---------------------------------------------------------


async def _log_consulta(
    db: AsyncSession,
    *,
    fonte: str,
    chave: str,
    sucesso: bool,
    error: str | None = None,
    employee_id: int | None = None,
) -> None:
    """LGPD: registra toda consulta a API externa que envolva dado
    pessoal/empresarial. Falhar ao gravar log nao bloqueia a consulta."""
    try:
        db.add(
            DossieConsultaLog(
                employee_id=employee_id,
                fonte=fonte,
                chave_consulta=chave,
                sucesso=sucesso,
                error_message=(error or None) if not sucesso else None,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("falha ao gravar log de consulta dossie")
        await db.rollback()


async def lookup_cep(
    db: AsyncSession,
    cep: str,
    *,
    viacep: ViaCEPClient,
    employee_id: int | None = None,
) -> dict[str, Any]:
    """Consulta CEP no ViaCEP, registrando log."""
    try:
        result = await viacep.get_endereco(cep)
        await _log_consulta(
            db,
            fonte="viacep",
            chave=cep,
            sucesso=True,
            employee_id=employee_id,
        )
        return result
    except (ViaCEPNotFoundError, ViaCEPError, ValueError) as exc:
        await _log_consulta(
            db,
            fonte="viacep",
            chave=cep,
            sucesso=False,
            error=str(exc),
            employee_id=employee_id,
        )
        raise


async def lookup_cnpj(
    db: AsyncSession,
    cnpj: str,
    *,
    brasilapi: BrasilAPIClient,
    employee_id: int | None = None,
) -> dict[str, Any]:
    try:
        result = await brasilapi.get_cnpj(cnpj)
        await _log_consulta(
            db,
            fonte="brasilapi_cnpj",
            chave=cnpj,
            sucesso=True,
            employee_id=employee_id,
        )
        return result
    except (BrasilAPINotFoundError, BrasilAPIError, ValueError) as exc:
        await _log_consulta(
            db,
            fonte="brasilapi_cnpj",
            chave=cnpj,
            sucesso=False,
            error=str(exc),
            employee_id=employee_id,
        )
        raise


async def lookup_cpf(
    db: AsyncSession,
    cpf: str,
    *,
    directdata: DirectDataClient,
    employee_id: int | None = None,
) -> dict[str, Any]:
    cpf_norm = normalize_cpf(cpf)
    if not is_valid_cpf(cpf_norm):
        await _log_consulta(
            db,
            fonte="directdata",
            chave=cpf_norm,
            sucesso=False,
            error="CPF invalido (digitos verificadores)",
            employee_id=employee_id,
        )
        raise ValueError("CPF invalido")
    try:
        result = await directdata.consultar_cpf(cpf_norm)
        await _log_consulta(
            db,
            fonte=result.get("source", "directdata"),
            chave=cpf_norm,
            sucesso=True,
            employee_id=employee_id,
        )
        return result
    except (DirectDataError, ValueError) as exc:
        await _log_consulta(
            db,
            fonte="directdata",
            chave=cpf_norm,
            sucesso=False,
            error=str(exc),
            employee_id=employee_id,
        )
        raise
