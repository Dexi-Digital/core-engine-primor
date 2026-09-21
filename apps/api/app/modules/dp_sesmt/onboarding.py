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
from app.integrations.onsafety.client import (
    OnsafetyClient,
    OnsafetyError,
    montar_body_trabalhador,
)
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
    projeto_id: str | None = None,
) -> OnboardingSyncRun:
    """Push de um funcionario para a OnSafety. Sempre devolve uma row
    (ok ou erro); nunca levanta erro de upstream.

    `projeto_id` (ONSAFETY_PROJETO_ID) vincula o trabalhador a um
    estabelecimento OnSafety -- sem ele o push real e recusado (403 de
    negocio) e vira row erro, visivel na UI.
    """
    correlation_id = f"{SISTEMA_ONSAFETY}-emp-{employee.id}"
    try:
        result = await client.create_or_update_trabalhador(
            nome=employee.nome_completo,
            cpf=employee.cpf,
            codigo_externo=str(employee.id),
            projeto_id=projeto_id,
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


# --- pre-visualizacao: ver as etapas SEM enviar -----------------------------
#
# O token disponivel e de PRODUCAO e a escrita fica atras do guard. Isto
# mostra o que SERIA enviado e em que etapa o envio pararia -- para a
# jornada ser vista de ponta a ponta antes de alguem decidir ligar.

OK, ATENCAO, BLOQUEADO, PENDENTE = "ok", "atencao", "bloqueado", "pendente"


def _etapa(chave: str, titulo: str, estado: str, detalhe: str | None = None) -> dict[str, Any]:
    return {"chave": chave, "titulo": titulo, "estado": estado, "detalhe": detalhe}


async def previsualizar_sync_onsafety(
    db: AsyncSession,
    client: OnsafetyClient,
    employee: Employee,
    *,
    projeto_id: str | None,
) -> dict[str, Any]:
    ambiente = "mock" if client.is_mock else ("producao" if client.is_prod else "homologacao")
    etapas: list[dict[str, Any]] = []

    # 1. dados minimos -- os mesmos que o push real valida
    payload: dict[str, Any] | None = None
    try:
        payload = montar_body_trabalhador(
            nome=employee.nome_completo,
            cpf=employee.cpf,
            codigo_externo=str(employee.id),
            projeto_id=projeto_id,
            matricula=employee.matricula,
            email=employee.email,
            data_admissao=employee.data_admissao.isoformat() if employee.data_admissao else None,
            data_nascimento=employee.data_nascimento.isoformat() if employee.data_nascimento else None,
        )
    except ValueError as exc:
        etapas.append(_etapa("dados", "Dados do funcionario", BLOQUEADO, str(exc)))
    else:
        faltas = []
        if not employee.data_admissao:
            faltas.append("sem data de admissao (a OnSafety aceita, mas o ASO admissional fica sem base)")
        if not employee.data_nascimento:
            faltas.append("sem data de nascimento")
        etapas.append(_etapa(
            "dados", "Dados do funcionario",
            ATENCAO if faltas else OK,
            "; ".join(faltas) if faltas else "nome, CPF valido e codigo externo prontos",
        ))

    # 2. estabelecimento -- sem ele a OnSafety devolve 403 (medido em homolog, 13/07)
    if projeto_id:
        etapas.append(_etapa("estabelecimento", "Estabelecimento na OnSafety", OK,
                             f"projeto {projeto_id} (ONSAFETY_PROJETO_ID)"))
    else:
        etapas.append(_etapa("estabelecimento", "Estabelecimento na OnSafety", BLOQUEADO,
                             "ONSAFETY_PROJETO_ID nao configurado: a OnSafety recusa o "
                             "cadastro com 403 'Estabelecimento nao especificado'"))

    # 3. guard de producao
    if client.is_mock:
        etapas.append(_etapa("guard", "Ambiente", ATENCAO,
                             "sem ONSAFETY_TOKEN: o envio seria SIMULADO, nada chega na OnSafety"))
    elif client.escrita_bloqueada:
        etapas.append(_etapa("guard", "Ambiente", BLOQUEADO, client.motivo_bloqueio_prod()))
    else:
        etapas.append(_etapa("guard", "Ambiente", OK,
                             "producao com escrita LIBERADA" if client.is_prod else "homologacao"))

    # 4 e 5. o que ainda nao aconteceu
    etapas.append(_etapa("envio", "Envio (create_or_update por CPF)", PENDENTE,
                         "upsert idempotente: N envios = 1 cadastro"))
    etapas.append(_etapa("confirmacao", "Confirmacao do id na OnSafety", PENDENTE,
                         "a OnSafety responde vazio; o id vem de uma busca por CPF na sequencia"))

    ultimo = None
    runs = await list_onboarding_runs(db, employee.id)
    if runs:
        r = runs[0]
        ultimo = {
            "status": r.status, "em": r.executed_at.isoformat() if r.executed_at else None,
            "external_id": r.external_id, "source": r.source, "erro": r.error_msg,
        }

    return {
        "sistema": SISTEMA_ONSAFETY,
        "ambiente": ambiente,
        "pode_enviar": payload is not None and not any(e["estado"] == BLOQUEADO for e in etapas),
        "payload": payload,
        "etapas": etapas,
        "ultimo_envio": ultimo,
    }


async def ultimo_run_por_employee(db: AsyncSession, employee_ids: list[int]) -> dict[int, OnboardingSyncRun]:
    """O ultimo push de cada funcionario, numa consulta so (para o painel)."""
    if not employee_ids:
        return {}
    rows = (
        await db.execute(
            select(OnboardingSyncRun)
            .where(OnboardingSyncRun.employee_id.in_(employee_ids))
            .where(OnboardingSyncRun.sistema == SISTEMA_ONSAFETY)
            .order_by(OnboardingSyncRun.employee_id, OnboardingSyncRun.id.desc())
        )
    ).scalars().all()
    ultimo: dict[int, OnboardingSyncRun] = {}
    for r in rows:
        ultimo.setdefault(r.employee_id, r)
    return ultimo
