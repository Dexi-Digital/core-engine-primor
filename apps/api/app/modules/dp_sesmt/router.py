"""Modulo A - Departamento Pessoal & SESMT.

Escopo (ver docs/roadmap.md):
    - Cadastro manual de funcionarios + dossie de admissao via APIs publicas.
    - Onboarding sync com Dominio/Onvio fica para um modulo separado.
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date

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
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.dp_sesmt import afastamentos as afastamentos_svc
from app.modules.dp_sesmt import service
from app.modules.dp_sesmt.afastamentos import (
    compute_dcb_status,
    compute_pericia_status,
)
from app.modules.dp_sesmt.aso_alerts import compute_aso_status
from app.modules.dp_sesmt.schemas import (
    AfastamentoCreate,
    AfastamentoRead,
    AfastamentoUpdate,
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
    setor: str | None = Query(
        None,
        max_length=128,
        description="Substring (ilike) do setor administrativo",
    ),
    is_admin_office: bool | None = Query(
        None,
        description=(
            "true = somente equipe administrativa (org do dossie); "
            "false = somente operacionais; omitir = todos"
        ),
    ),
    search: str | None = Query(None, max_length=128),
    aso_status: str | None = Query(
        None,
        max_length=16,
        description="Filtra por estado do ASO: vigente|vencendo|vencido|sem_validade",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows, total = await service.list_employees(
        db,
        status=status,
        obra=obra,
        setor=setor,
        is_admin_office=is_admin_office,
        search=search,
        aso_status=aso_status,
        limit=limit,
        offset=offset,
    )
    # Computa aso_status por linha. Mais barato fazer aqui que repetir
    # a logica em SQL/computed_field -- a UI usa este campo direto pra
    # renderizar a badge sem refazer arithmetic em JS.
    items = []
    for r in rows:
        item = EmployeeRead.model_validate(r).model_dump()
        item["aso_status"] = compute_aso_status(r.aso_validade)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/aso/alerts/dispatch", response_model=dict)
async def dispatch_aso_alerts_endpoint(
    recipients: list[str] | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Dispara manualmente o cron de alertas de ASO.

    Endpoint sincrono (nao usa Celery) -- util pra teste manual e pra
    UI que quer "Mandar lembrete agora" sem esperar 08h05 do dia
    seguinte. Em prod o cron roda 1x/dia automaticamente.

    Se `recipients` nao for informado, usa `ASO_ALERT_EMAILS` da env.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(503, "RESEND_API_KEY nao configurada")
    if recipients is None or len(recipients) == 0:
        env_val = os.getenv("ASO_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        raise HTTPException(
            422, "Nenhum destinatario informado e ASO_ALERT_EMAILS vazia"
        )
    from app.integrations.resend.client import ResendClient
    from app.modules.dp_sesmt.aso_alerts import dispatch_aso_alerts

    resend = ResendClient(api_key=settings.resend_api_key)
    try:
        summary = await dispatch_aso_alerts(db, resend, recipients=recipients)
    finally:
        await resend.aclose()
    return {
        "total_employees": summary.total_employees,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }


@router.post("/employees", response_model=EmployeeRead, status_code=201)
async def create_employee_endpoint(
    payload: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmployeeRead:
    existing = await service.get_employee_by_cpf(db, payload.cpf)
    if existing is not None:
        raise HTTPException(409, f"Ja existe funcionario com CPF {payload.cpf}")
    data = payload.model_dump(exclude={"empregos_anteriores"})
    employee = await service.create_employee(
        db,
        empregos_anteriores=payload.empregos_anteriores,
        actor=current_user.email,
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
    current_user: User = Depends(get_current_user),
) -> EmployeeRead:
    fields = payload.model_dump(exclude_unset=True)
    employee = await service.update_employee(
        db, employee_id, actor=current_user.email, **fields
    )
    if employee is None:
        raise HTTPException(404, f"Funcionario {employee_id} nao encontrado")
    return EmployeeRead.model_validate(employee)


@router.delete("/employees/{employee_id}", status_code=204)
async def delete_employee_endpoint(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = await service.delete_employee(
        db, employee_id, actor=current_user.email
    )
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


# --- Afastamentos INSS (D4) -------------------------------------------------


def _to_afastamento_read(
    row, *, today: _date | None = None
) -> AfastamentoRead:
    """Materializa um Afastamento ORM em AfastamentoRead com campos
    computados (`dcb_status`, `pericia_status`, dias restantes).

    Aceita `today` para garantir consistencia entre status e
    `dias_para_*` quando o request cruza meia-noite -- mesmo padrao
    de `_certidao_to_read` em `certidoes_router`.
    """
    today = today or _date.today()
    base = AfastamentoRead.model_validate(row).model_dump()
    base["dcb_status"] = compute_dcb_status(row.dcb, today=today)
    base["pericia_status"] = compute_pericia_status(
        row.data_pericia, today=today
    )
    base["dias_para_dcb"] = (
        (row.dcb - today).days if row.dcb is not None else None
    )
    base["dias_para_pericia"] = (
        (row.data_pericia - today).days
        if row.data_pericia is not None
        else None
    )
    return AfastamentoRead.model_validate(base)


@router.get("/afastamentos", response_model=list[AfastamentoRead])
async def list_afastamentos_endpoint(
    employee_id: int | None = Query(None),
    status: str | None = Query(None, max_length=32),
    db: AsyncSession = Depends(get_db),
) -> list[AfastamentoRead]:
    rows = await afastamentos_svc.list_afastamentos(
        db, employee_id=employee_id, status=status
    )
    today = _date.today()
    return [_to_afastamento_read(r, today=today) for r in rows]


@router.post(
    "/afastamentos", response_model=AfastamentoRead, status_code=201
)
async def create_afastamento_endpoint(
    payload: AfastamentoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AfastamentoRead:
    row = await afastamentos_svc.create_afastamento(
        db,
        actor=current_user.email,
        **payload.model_dump(),
    )
    if row is None:
        raise HTTPException(
            404, f"Funcionario {payload.employee_id} nao encontrado"
        )
    return _to_afastamento_read(row)


@router.get(
    "/afastamentos/{afastamento_id}", response_model=AfastamentoRead
)
async def get_afastamento_endpoint(
    afastamento_id: int, db: AsyncSession = Depends(get_db)
) -> AfastamentoRead:
    row = await afastamentos_svc.get_afastamento(db, afastamento_id)
    if row is None:
        raise HTTPException(
            404, f"Afastamento {afastamento_id} nao encontrado"
        )
    return _to_afastamento_read(row)


@router.put(
    "/afastamentos/{afastamento_id}", response_model=AfastamentoRead
)
async def update_afastamento_endpoint(
    afastamento_id: int,
    payload: AfastamentoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AfastamentoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await afastamentos_svc.update_afastamento(
        db, afastamento_id, actor=current_user.email, **fields
    )
    if row is None:
        raise HTTPException(
            404, f"Afastamento {afastamento_id} nao encontrado"
        )
    return _to_afastamento_read(row)


@router.delete("/afastamentos/{afastamento_id}", status_code=204)
async def delete_afastamento_endpoint(
    afastamento_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = await afastamentos_svc.delete_afastamento(
        db, afastamento_id, actor=current_user.email
    )
    if not deleted:
        raise HTTPException(
            404, f"Afastamento {afastamento_id} nao encontrado"
        )


@router.post("/afastamentos/alerts/dispatch", response_model=dict)
async def dispatch_afastamento_alerts_endpoint(
    recipients: list[str] | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Dispara manualmente os alertas de DCB/pericia de afastamentos.

    Em prod o cron roda 1x/dia (08h10). Se `recipients` nao for
    informado, usa `INSS_ALERT_EMAILS` da env (mesmo padrao do A.2).
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(503, "RESEND_API_KEY nao configurada")
    if recipients is None or len(recipients) == 0:
        env_val = os.getenv("INSS_ALERT_EMAILS", "").strip()
        recipients = [e.strip() for e in env_val.split(",") if e.strip()]
    if not recipients:
        raise HTTPException(
            422,
            "Nenhum destinatario informado e INSS_ALERT_EMAILS vazia",
        )
    from app.integrations.resend.client import ResendClient

    resend = ResendClient(api_key=settings.resend_api_key)
    try:
        summary = await afastamentos_svc.dispatch_afastamento_alerts(
            db, resend, recipients=recipients
        )
    finally:
        await resend.aclose()
    return {
        "total_afastamentos": summary.total_afastamentos,
        "sent": summary.sent,
        "skipped": summary.skipped,
        "failed": summary.failed,
    }
