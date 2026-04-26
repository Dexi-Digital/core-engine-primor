"""Runner do motor de Diagnostico Documental (D1).

Itera todas as entidades cadastradas (funcionarios ativos, veiculos
ativos, empresas com certidoes, obras ativas) e avalia cada
`DocumentRequirement` do checklist da area, gerando 1 row em
`DiagnosticoFinding` por (entidade, doc obrigatorio).

Uso tipico:

    from app.modules.diagnostico.runner import run_diagnostico
    run = await run_diagnostico(db, scope="all", actor="admin@primor.com")

Status do finding e calculado a partir da row mais recente do banco
para aquela combinacao (entidade, doc_tipo). ASO em `Employee` e
caso especial -- a UI mantem `aso_validade` na propria row do
funcionario por compat com A.2.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as _date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.modules.diagnostico.checklists import (
    CHECKLIST_EMPRESA,
    CHECKLIST_FROTA_VEICULO,
    CHECKLIST_OBRA,
    applicable_dp_requirements,
)
from app.modules.diagnostico.models import (
    AREA_DP,
    AREA_EMPRESA,
    AREA_FROTA,
    AREA_OBRA,
    AREA_SST,
    FINDING_AUSENTE,
    FINDING_OK,
    FINDING_VENCENDO,
    FINDING_VENCIDO,
    RUN_DONE,
    RUN_ERROR,
    VENCENDO_THRESHOLD_DIAS,
    DiagnosticoFinding,
    DiagnosticoRun,
)
from app.modules.dp_sesmt.models import (
    STATUS_DESLIGADO,
    Employee,
    EmployeeDocument,
)
from app.modules.licitacoes.models import (
    CertidaoEmpresa,
    EmpresaDocumento,
)
from app.modules.manutencao_frota.models import (
    STATUS_ATIVO as STATUS_VEICULO_ATIVO,
)
from app.modules.manutencao_frota.models import (
    DocumentoVeiculo,
    Veiculo,
)
from app.modules.obras.models import (
    STATUS_ATIVA,
    Obra,
    ObraDocumento,
)

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "diagnostico.run"


# ----------------------------- doc presence helpers -----------------------


@dataclass(slots=True)
class _Presenca:
    """Resultado da consulta 'tem doc desse tipo para essa entidade?'."""

    presente: bool
    validade: _date | None
    numero: str | None = None


def _evaluate_status(
    presence: _Presenca, *, today: _date
) -> tuple[str, int | None]:
    """Calcula `status` e `dias_para_vencimento` para um requirement.

    - presence.presente=False -> ausente
    - validade None (perene)  -> ok (presente, sem prazo)
    - validade < hoje          -> vencido
    - validade <= hoje + 30    -> vencendo
    - else                     -> ok
    """
    if not presence.presente:
        return FINDING_AUSENTE, None
    if presence.validade is None:
        return FINDING_OK, None
    delta = (presence.validade - today).days
    if delta < 0:
        return FINDING_VENCIDO, delta
    if delta <= VENCENDO_THRESHOLD_DIAS:
        return FINDING_VENCENDO, delta
    return FINDING_OK, delta


# ----------------------------- finders por area ---------------------------


async def _employee_doc_index(
    db: AsyncSession,
) -> dict[tuple[int, str], _Presenca]:
    """Pre-carrega documentos por (employee_id, tipo) -- mais recente."""
    res = await db.execute(
        select(EmployeeDocument).order_by(
            EmployeeDocument.employee_id,
            EmployeeDocument.tipo,
            EmployeeDocument.id.desc(),
        )
    )
    out: dict[tuple[int, str], _Presenca] = {}
    for row in res.scalars().all():
        key = (row.employee_id, row.tipo)
        if key not in out:  # mais recente primeiro
            out[key] = _Presenca(
                presente=True,
                validade=row.validade,
                numero=row.numero,
            )
    return out


async def _veiculo_doc_index(
    db: AsyncSession,
) -> dict[tuple[int, str], _Presenca]:
    res = await db.execute(
        select(DocumentoVeiculo).order_by(
            DocumentoVeiculo.veiculo_id,
            DocumentoVeiculo.tipo,
            DocumentoVeiculo.id.desc(),
        )
    )
    out: dict[tuple[int, str], _Presenca] = {}
    for row in res.scalars().all():
        key = (row.veiculo_id, row.tipo)
        if key not in out:
            out[key] = _Presenca(
                presente=True,
                validade=row.validade,
                numero=row.numero,
            )
    return out


async def _certidao_index(
    db: AsyncSession,
) -> dict[tuple[str, str], _Presenca]:
    """Index por (empresa_cnpj, tipo) -- so a certidao mais recente."""
    res = await db.execute(
        select(CertidaoEmpresa).order_by(
            CertidaoEmpresa.empresa_cnpj,
            CertidaoEmpresa.tipo,
            CertidaoEmpresa.id.desc(),
        )
    )
    out: dict[tuple[str, str], _Presenca] = {}
    for row in res.scalars().all():
        key = (row.empresa_cnpj, row.tipo)
        if key not in out:
            out[key] = _Presenca(
                presente=True, validade=row.validade, numero=row.numero
            )
    return out


async def _empresa_documento_index(
    db: AsyncSession,
) -> dict[tuple[str, str], _Presenca]:
    res = await db.execute(
        select(EmpresaDocumento).order_by(
            EmpresaDocumento.empresa_cnpj,
            EmpresaDocumento.tipo,
            EmpresaDocumento.id.desc(),
        )
    )
    out: dict[tuple[str, str], _Presenca] = {}
    for row in res.scalars().all():
        key = (row.empresa_cnpj, row.tipo)
        if key not in out:
            out[key] = _Presenca(
                presente=True, validade=row.validade, numero=row.numero
            )
    return out


async def _obra_doc_index(
    db: AsyncSession,
) -> dict[tuple[int, str], _Presenca]:
    res = await db.execute(
        select(ObraDocumento).order_by(
            ObraDocumento.obra_id,
            ObraDocumento.tipo,
            ObraDocumento.id.desc(),
        )
    )
    out: dict[tuple[int, str], _Presenca] = {}
    for row in res.scalars().all():
        key = (row.obra_id, row.tipo)
        if key not in out:
            out[key] = _Presenca(
                presente=True, validade=row.validade, numero=row.numero
            )
    return out


# ----------------------------- main runner --------------------------------


@dataclass(slots=True)
class _FindingRow:
    """Snapshot detached -- evita MissingGreenlet apos rollback."""

    area: str
    entity_type: str
    entity_id: int | None
    entity_label: str
    doc_tipo: str
    doc_label: str
    status: str
    validade: _date | None
    dias_para_vencimento: int | None
    message: str | None


def _evaluate_dp_employee(
    emp: Employee,
    emp_docs: dict[tuple[int, str], _Presenca],
    *,
    today: _date,
) -> list[_FindingRow]:
    rows: list[_FindingRow] = []
    label = f"{emp.nome_completo} ({emp.cpf})"

    for req in applicable_dp_requirements(emp):
        # ASO em DP usa as colunas flat de Employee (compat A.2).
        if req.area == AREA_DP and req.doc_tipo == "ASO":
            if emp.aso_data is None:
                presence = _Presenca(presente=False, validade=None)
            else:
                presence = _Presenca(
                    presente=True, validade=emp.aso_validade
                )
        else:
            presence = emp_docs.get(
                (emp.id, req.doc_tipo), _Presenca(presente=False, validade=None)
            )
        status, delta = _evaluate_status(presence, today=today)
        rows.append(
            _FindingRow(
                area=req.area,
                entity_type="employee",
                entity_id=emp.id,
                entity_label=label,
                doc_tipo=req.doc_tipo,
                doc_label=req.doc_label,
                status=status,
                validade=presence.validade,
                dias_para_vencimento=delta,
                message=None,
            )
        )
    return rows


def _evaluate_veiculo(
    veiculo: Veiculo,
    veiculo_docs: dict[tuple[int, str], _Presenca],
    *,
    today: _date,
) -> list[_FindingRow]:
    rows: list[_FindingRow] = []
    label = f"{veiculo.placa} ({veiculo.marca or ''} {veiculo.modelo or ''})".strip()
    for req in CHECKLIST_FROTA_VEICULO:
        presence = veiculo_docs.get(
            (veiculo.id, req.doc_tipo),
            _Presenca(presente=False, validade=None),
        )
        status, delta = _evaluate_status(presence, today=today)
        rows.append(
            _FindingRow(
                area=req.area,
                entity_type="veiculo",
                entity_id=veiculo.id,
                entity_label=label,
                doc_tipo=req.doc_tipo,
                doc_label=req.doc_label,
                status=status,
                validade=presence.validade,
                dias_para_vencimento=delta,
                message=None,
            )
        )
    return rows


def _evaluate_empresa(
    cnpj: str,
    cert_index: dict[tuple[str, str], _Presenca],
    emp_doc_index: dict[tuple[str, str], _Presenca],
    *,
    today: _date,
) -> list[_FindingRow]:
    rows: list[_FindingRow] = []
    for req in CHECKLIST_EMPRESA:
        # Certidoes (CND_*, FGTS, CNDT, INSS, ESTADUAL, MUNICIPAL)
        # ficam em `certidoes_empresa`. Os demais (contrato social,
        # SICAF, ...) ficam em `empresa_documentos`.
        if req.doc_tipo in {
            "CND_FEDERAL",
            "FGTS",
            "CNDT",
            "INSS",
            "ESTADUAL",
            "MUNICIPAL",
        }:
            presence = cert_index.get(
                (cnpj, req.doc_tipo), _Presenca(presente=False, validade=None)
            )
        else:
            presence = emp_doc_index.get(
                (cnpj, req.doc_tipo), _Presenca(presente=False, validade=None)
            )
        status, delta = _evaluate_status(presence, today=today)
        rows.append(
            _FindingRow(
                area=req.area,
                entity_type="empresa",
                entity_id=None,
                entity_label=cnpj,
                doc_tipo=req.doc_tipo,
                doc_label=req.doc_label,
                status=status,
                validade=presence.validade,
                dias_para_vencimento=delta,
                message=None,
            )
        )
    return rows


def _evaluate_obra(
    obra: Obra,
    obra_docs: dict[tuple[int, str], _Presenca],
    *,
    today: _date,
) -> list[_FindingRow]:
    rows: list[_FindingRow] = []
    label = f"{obra.codigo} -- {obra.nome}"
    for req in CHECKLIST_OBRA:
        presence = obra_docs.get(
            (obra.id, req.doc_tipo),
            _Presenca(presente=False, validade=None),
        )
        status, delta = _evaluate_status(presence, today=today)
        rows.append(
            _FindingRow(
                area=req.area,
                entity_type="obra",
                entity_id=obra.id,
                entity_label=label,
                doc_tipo=req.doc_tipo,
                doc_label=req.doc_label,
                status=status,
                validade=presence.validade,
                dias_para_vencimento=delta,
                message=None,
            )
        )
    return rows


async def _empresa_cnpjs(db: AsyncSession) -> list[str]:
    """Lista CNPJs distintos com pelo menos 1 doc cadastrado."""
    res = await db.execute(select(CertidaoEmpresa.empresa_cnpj).distinct())
    cnpjs: set[str] = {r[0] for r in res.all() if r[0]}
    res2 = await db.execute(select(EmpresaDocumento.empresa_cnpj).distinct())
    cnpjs.update({r[0] for r in res2.all() if r[0]})
    return sorted(cnpjs)


def _summarize(rows: list[_FindingRow]) -> dict[str, dict[str, int]]:
    """Agrega contagens por area + status para o `summary_json` do run."""
    summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            FINDING_OK: 0,
            FINDING_VENCENDO: 0,
            FINDING_VENCIDO: 0,
            FINDING_AUSENTE: 0,
            "total": 0,
        }
    )
    for r in rows:
        summary[r.area][r.status] += 1
        summary[r.area]["total"] += 1
    return {k: dict(v) for k, v in summary.items()}


async def run_diagnostico(
    db: AsyncSession,
    *,
    scope: str = "all",
    actor: str = "system",
    today: _date | None = None,
) -> DiagnosticoRun:
    """Executa o diagnostico e persiste run + findings.

    `scope` filtra quais areas serao avaliadas (`all` | `dp` | `sst` |
    `frota` | `empresa` | `obra`). DP e SST sempre andam juntas pois
    ambas iteram sobre a mesma colecao de funcionarios -- pedir so
    "sst" inclui DP no run mas grava findings de ambas.
    """
    today = today or _date.today()
    run = DiagnosticoRun(scope=scope, triggered_by=actor)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    try:
        all_findings: list[_FindingRow] = []

        if scope in ("all", "dp", "sst"):
            emp_docs = await _employee_doc_index(db)
            res = await db.execute(
                select(Employee).where(Employee.status != STATUS_DESLIGADO)
            )
            employees = list(res.scalars().all())
            for emp in employees:
                all_findings.extend(
                    _evaluate_dp_employee(emp, emp_docs, today=today)
                )

        if scope in ("all", "frota"):
            veiculo_docs = await _veiculo_doc_index(db)
            res = await db.execute(
                select(Veiculo).where(Veiculo.status == STATUS_VEICULO_ATIVO)
            )
            for v in res.scalars().all():
                all_findings.extend(
                    _evaluate_veiculo(v, veiculo_docs, today=today)
                )

        if scope in ("all", "empresa"):
            cert_index = await _certidao_index(db)
            emp_doc_index = await _empresa_documento_index(db)
            for cnpj in await _empresa_cnpjs(db):
                all_findings.extend(
                    _evaluate_empresa(
                        cnpj, cert_index, emp_doc_index, today=today
                    )
                )

        if scope in ("all", "obra"):
            obra_docs = await _obra_doc_index(db)
            res = await db.execute(
                select(Obra).where(Obra.status == STATUS_ATIVA)
            )
            for obra in res.scalars().all():
                all_findings.extend(
                    _evaluate_obra(obra, obra_docs, today=today)
                )

        # Persiste findings em batch
        for f in all_findings:
            db.add(
                DiagnosticoFinding(
                    run_id=run.id,
                    area=f.area,
                    entity_type=f.entity_type,
                    entity_id=f.entity_id,
                    entity_label=f.entity_label,
                    doc_tipo=f.doc_tipo,
                    doc_label=f.doc_label,
                    status=f.status,
                    validade=f.validade,
                    dias_para_vencimento=f.dias_para_vencimento,
                    message=f.message,
                )
            )

        # Atualiza contadores agregados na run
        run.total_findings = len(all_findings)
        run.ok_count = sum(1 for f in all_findings if f.status == FINDING_OK)
        run.ausente_count = sum(
            1 for f in all_findings if f.status == FINDING_AUSENTE
        )
        run.vencido_count = sum(
            1 for f in all_findings if f.status == FINDING_VENCIDO
        )
        run.vencendo_count = sum(
            1 for f in all_findings if f.status == FINDING_VENCENDO
        )
        run.summary_json = _summarize(all_findings)
        run.status = RUN_DONE
        run.finished_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(run)

        # Audit
        db.add(
            AuditLog(
                actor=actor,
                action="run",
                resource=_AUDIT_RESOURCE,
                resource_id=str(run.id),
                metadata_json=json.dumps(
                    {
                        "scope": scope,
                        "total": run.total_findings,
                        "ok": run.ok_count,
                        "ausente": run.ausente_count,
                        "vencido": run.vencido_count,
                        "vencendo": run.vencendo_count,
                    }
                ),
            )
        )
        await db.commit()
        return run

    except Exception as exc:  # noqa: BLE001
        logger.exception("diagnostico run failed: %s", exc)
        run.status = RUN_ERROR
        run.error_message = str(exc)[:1000]
        run.finished_at = datetime.now(UTC)
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
        raise


# Aliases para uso externo (test, router) -- mantem _FindingRow como
# detalhe de implementacao.
__all__ = ["run_diagnostico"]


_AREAS_SUPORTADAS = (AREA_DP, AREA_SST, AREA_FROTA, AREA_EMPRESA, AREA_OBRA)
