"""Pull SST da OnSafety para o dossie do funcionario (Squad 2, ADR-001).

Traz ASOs (exames ocupacionais) e fichas de EPI da OnSafety e
materializa no dossie:

- ASO -> colunas `aso_data`/`aso_validade`/`aso_resultado` de
  `dp_employees`. Essas colunas alimentam os alertas A.2 EM PRODUCAO
  (cron 08h05) -- o pull roda 07h30 para os alertas usarem dado fresco.
- Fichas de EPI -> rows `EmployeeDocument` tipo `FICHA_EPI` com
  `source="onsafety"` e `onsafety_external_id` = id do controle na
  OnSafety (idempotencia: re-pull atualiza em vez de duplicar).

Regras de protecao:

- **Matching por CPF** (chave natural de `dp_employees`): o CPF vem
  normalizado do adapter (11 digitos) e passa por `is_valid_cpf` antes
  do lookup. Sem match -> contado em `*_no_match`, nunca cria
  funcionario (cadastro e responsabilidade do RH/onboarding).
- **ASO nunca regride**: so atualiza quando `data_aso` da OnSafety e
  mais recente que a atual do dossie (ou quando o dossie nao tem ASO).
  Dado manual mais novo nao e sobrescrito por pull atrasado.
- **LGPD**: cada funcionario tocado ganha uma row em
  `dp_dossie_consultas` (fonte `onsafety_aso`/`onsafety_epi`) -- ASO e
  dado de saude. Resumo do run vai para `audit_log`.
- Erros de upstream (OnsafetyError) NAO propagam: o summary volta com
  `error` preenchido e o que ja foi processado fica commitado.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.core.cpf import is_valid_cpf
from app.integrations.onsafety.client import OnsafetyClient, OnsafetyError
from app.modules.dp_sesmt.models import (
    DOC_EMP_FICHA_EPI,
    DossieConsultaLog,
    Employee,
    EmployeeDocument,
)
from app.modules.dp_sesmt.service import get_employee_by_cpf

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "dp_sesmt.onsafety_pull"
_PAGE_SIZE = 200

# resultado_aso da OnSafety e um int32 SEM enum/descricao no spec
# OpenAPI e a base de homolog esta vazia -- este mapeamento e
# ASSUMIDO, nao confirmado. NAO fazer rollout em producao antes da
# confirmacao da OnSafety (pergunta registrada na issue #39): e dado
# de saude exibido ao RH -- rotulo invertido e pior que nenhum.
# Valores fora do mapa ficam como string do numero (nao inventamos
# semantica) e contam em `aso_resultado_desconhecido`.
_RESULTADO_MAP = {1: "apto", 2: "inapto", 3: "apto_restricoes"}


@dataclass
class PullSummary:
    source: str = "onsafety"
    # ASO
    exames_total: int = 0
    aso_updated: int = 0
    aso_skipped_older: int = 0
    aso_sem_data: int = 0
    aso_no_match: int = 0
    aso_resultado_desconhecido: int = 0
    # EPI
    epis_total: int = 0
    epis_created: int = 0
    epis_updated: int = 0
    epis_no_match: int = 0
    # geral
    cpfs_invalidos: int = 0
    datas_invalidas: int = 0
    error: str | None = None
    consultas_logadas: int = 0


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_date_counted(value: Any, summary: PullSummary) -> date | None:
    """Como `_parse_date`, mas conta string nao-vazia que falhou o parse.

    `validade` do controle de EPI e string LIVRE no spec (nao
    date-time) e nao foi validada em homolog (base sem EPIs). Se vier
    em formato BR ("30/05/2026"), sem este contador todas as fichas
    ficariam sem validade EM SILENCIO -- e o diagnostico documental
    deixaria de alertar vencimento.
    """
    parsed = _parse_date(value)
    if parsed is None and value:
        summary.datas_invalidas += 1
    return parsed


async def _iter_paginado(fetch: Any) -> list[dict[str, Any]]:
    """Percorre todas as paginas de um `list_*` do adapter."""
    items: list[dict[str, Any]] = []
    page = 0
    while True:
        res = await fetch(page=page, size=_PAGE_SIZE)
        items.extend(res["items"])
        if not res["items"] or len(items) >= res["total"]:
            return items
        page += 1


async def _log_consulta(
    db: AsyncSession,
    *,
    employee_id: int,
    fonte: str,
    cpf: str,
    ja_logados: set[tuple[int, str]],
) -> bool:
    """Uma row LGPD por (employee, fonte) por run -- nao por item."""
    key = (employee_id, fonte)
    if key in ja_logados:
        return False
    ja_logados.add(key)
    db.add(
        DossieConsultaLog(
            employee_id=employee_id,
            fonte=fonte,
            chave_consulta=cpf,
            sucesso=True,
        )
    )
    return True


async def _match_employee(
    db: AsyncSession, item: dict[str, Any]
) -> tuple[Employee | None, str | None]:
    """Devolve (employee, motivo_da_falha).

    Motivos disjuntos -- `cpfs_invalidos` e `*_no_match` do summary
    NAO se sobrepoem: "invalido" = CPF reprovado em is_valid_cpf;
    "no_match" = CPF valido sem funcionario correspondente.
    """
    trabalhador = item.get("trabalhador") or {}
    cpf = trabalhador.get("cpf") or ""
    if not is_valid_cpf(cpf):
        return None, "invalido"
    employee = await get_employee_by_cpf(db, cpf)
    if employee is None:
        return None, "no_match"
    return employee, None


async def pull_asos(
    db: AsyncSession,
    client: OnsafetyClient,
    summary: PullSummary,
    ja_logados: set[tuple[int, str]],
) -> None:
    exames = await _iter_paginado(client.list_exames_ocupacionais)
    summary.exames_total = len(exames)
    for exame in exames:
        employee, motivo = await _match_employee(db, exame)
        if employee is None:
            if motivo == "invalido":
                summary.cpfs_invalidos += 1
            else:
                summary.aso_no_match += 1
            continue
        if await _log_consulta(
            db,
            employee_id=employee.id,
            fonte="onsafety_aso",
            cpf=(exame.get("trabalhador") or {}).get("cpf") or "",
            ja_logados=ja_logados,
        ):
            summary.consultas_logadas += 1

        data_aso = _parse_date_counted(exame.get("data_aso"), summary)
        if data_aso is None:
            summary.aso_sem_data += 1
            continue
        # ASO nunca regride: pull atrasado nao sobrescreve dado manual
        # (ou de pull anterior) mais recente.
        if employee.aso_data is not None and data_aso <= employee.aso_data:
            summary.aso_skipped_older += 1
            continue

        resultado_raw = exame.get("resultado_aso")
        resultado = _RESULTADO_MAP.get(resultado_raw)
        if resultado is None and resultado_raw is not None:
            summary.aso_resultado_desconhecido += 1
            resultado = str(resultado_raw)[:16]

        employee.aso_data = data_aso
        employee.aso_validade = _parse_date_counted(
            exame.get("data_vencimento_aso"), summary
        )
        employee.aso_resultado = resultado
        summary.aso_updated += 1
    await db.commit()


async def pull_epis(
    db: AsyncSession,
    client: OnsafetyClient,
    summary: PullSummary,
    ja_logados: set[tuple[int, str]],
) -> None:
    controles = await _iter_paginado(client.list_controles_epi)
    summary.epis_total = len(controles)
    for controle in controles:
        employee, motivo = await _match_employee(db, controle)
        if employee is None:
            if motivo == "invalido":
                summary.cpfs_invalidos += 1
            else:
                summary.epis_no_match += 1
            continue
        if await _log_consulta(
            db,
            employee_id=employee.id,
            fonte="onsafety_epi",
            cpf=(controle.get("trabalhador") or {}).get("cpf") or "",
            ja_logados=ja_logados,
        ):
            summary.consultas_logadas += 1

        external_id = str(controle.get("id"))
        stmt = select(EmployeeDocument).where(
            EmployeeDocument.onsafety_external_id == external_id
        )
        doc = (await db.execute(stmt)).scalar_one_or_none()

        ca = controle.get("ca")
        quantidade = controle.get("quantidade")
        observacoes = (
            f"{controle.get('nome_equipamento') or 'EPI'}"
            f" (qtd {quantidade})" if quantidade is not None else ""
        ) or None
        fields = {
            "employee_id": employee.id,
            "tipo": DOC_EMP_FICHA_EPI,
            "numero": f"CA {ca}" if ca is not None else None,
            "emissao": _parse_date_counted(
                controle.get("data_entrega"), summary
            ),
            "validade": _parse_date_counted(
                controle.get("validade"), summary
            ),
            "observacoes": observacoes,
            "source": "onsafety",
            "onsafety_external_id": external_id,
        }
        if doc is None:
            db.add(EmployeeDocument(**fields))
            summary.epis_created += 1
        else:
            # Docs do sync sao espelho da OnSafety -- atualizamos in
            # place (rows manuais, source="manual", nunca sao tocadas
            # porque nao tem onsafety_external_id).
            for k, v in fields.items():
                setattr(doc, k, v)
            summary.epis_updated += 1
    await db.commit()


async def pull_onsafety(
    db: AsyncSession,
    client: OnsafetyClient,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> PullSummary:
    """Roda o pull completo (ASOs + EPIs). Nunca propaga OnsafetyError."""
    summary = PullSummary()
    ja_logados: set[tuple[int, str]] = set()
    try:
        await pull_asos(db, client, summary, ja_logados)
        await pull_epis(db, client, summary, ja_logados)
    except OnsafetyError as exc:
        logger.warning("pull onsafety interrompido: %s", exc)
        summary.error = str(exc)[:500]
        await db.commit()  # preserva o que ja foi processado

    db.add(
        AuditLog(
            actor=actor,
            action="pull" if summary.error is None else "error",
            resource=_AUDIT_RESOURCE,
            resource_id=None,
            metadata_json=json.dumps(asdict(summary), default=str),
        )
    )
    await db.commit()
    return summary
