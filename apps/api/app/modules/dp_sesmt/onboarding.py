"""Push de onboarding para a OnSafety (etapa 3, ADR-001).

Envia um funcionario do Motor Central para a OnSafety via
`create_or_update_trabalhador`, com `codigoExterno` = nosso employee id
(chave de reconciliacao/idempotencia -- a OnSafety faz upsert, rodar N
vezes nao duplica). Dominio/Onvio/Tangerino entram aqui no futuro, cada
um como um `sistema` distinto em `dp_onboarding_syncs`.

Tratamento de erros (padrao B.3 `consultar_detran`): NUNCA propaga
`OnsafetyError` para o router -- qualquer falha (transporte, auth,
guard de prod, CPF invalido no cadastro) vira uma row `status="erro"`
com `error_msg` truncado, e o funcionario fica disponivel para retry
pela UI. Excecao de programacao (bug) continua subindo.

Guard de producao: o token disponivel hoje e o de PROD (ADR-001). O
adapter bloqueia escrita contra `api.onsafety.com.br` sem
`ONSAFETY_ALLOW_PROD_WRITE=true`; o bloqueio aparece aqui como uma row
`status="erro"` explicando o motivo -- visivel na UI, sem stacktrace.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.integrations.onsafety.client import OnsafetyClient, OnsafetyError
from app.modules.dp_sesmt.models import Employee, OnboardingSyncRun

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "dp_sesmt.onboarding_sync"
SISTEMA_ONSAFETY = "onsafety"


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str)
            if metadata
            else None,
        )
    )
    await db.commit()


async def sync_employee_onsafety(
    db: AsyncSession,
    client: OnsafetyClient,
    employee: Employee,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
    is_editing: bool = False,
) -> OnboardingSyncRun:
    """Push de um funcionario para a OnSafety. Sempre devolve uma row
    (ok ou erro); nunca levanta erro de upstream."""
    correlation_id = f"{SISTEMA_ONSAFETY}-emp-{employee.id}"
    try:
        result = await client.create_or_update_trabalhador(
            nome=employee.nome_completo,
            cpf=employee.cpf,
            codigo_externo=str(employee.id),
            matricula=employee.matricula,
            email=employee.email,
            data_admissao=employee.data_admissao.isoformat()
            if employee.data_admissao
            else None,
            data_nascimento=employee.data_nascimento.isoformat()
            if employee.data_nascimento
            else None,
            is_editing=is_editing,
        )
        run = OnboardingSyncRun(
            employee_id=employee.id,
            sistema=SISTEMA_ONSAFETY,
            correlation_id=correlation_id,
            status="ok",
            external_id=result.get("id"),
            source=result.get("source"),
        )
        audit_action = "create"
        audit_meta: dict[str, Any] = {
            "correlation_id": correlation_id,
            "external_id": result.get("id"),
            "source": result.get("source"),
        }
    except (OnsafetyError, ValueError) as exc:
        # ValueError cobre cadastro invalido (ex.: CPF sem 11 digitos
        # em row legada) -- vira erro visivel na UI, nao 500.
        logger.warning(
            "onboarding onsafety falhou (employee=%s): %s",
            employee.id,
            exc,
        )
        run = OnboardingSyncRun(
            employee_id=employee.id,
            sistema=SISTEMA_ONSAFETY,
            correlation_id=correlation_id,
            status="erro",
            error_msg=str(exc)[:500],
        )
        audit_action = "error"
        audit_meta = {
            "correlation_id": correlation_id,
            "error": str(exc)[:500],
        }

    db.add(run)
    await db.commit()
    await db.refresh(run)
    await _record_audit(
        db,
        action=audit_action,
        resource_id=run.id,
        metadata={"employee_id": employee.id, **audit_meta},
        actor=actor,
    )
    return run


async def list_onboarding_runs(
    db: AsyncSession, employee_id: int
) -> list[OnboardingSyncRun]:
    stmt = (
        select(OnboardingSyncRun)
        .where(OnboardingSyncRun.employee_id == employee_id)
        .order_by(OnboardingSyncRun.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())
