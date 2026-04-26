"""Servico do sync OneDrive -> tabelas de documentos (D1 fase 2).

Fluxo:
    1. Cria `OneDriveSyncRun` com status='running'.
    2. Chama `client.list_folder(recursive=True)` na raiz.
    3. Para cada item, `parse_path` -> `ParsedMatch` (ou skip/error).
    4. Resolve `entity_id` a partir de `entity_key` (employee_id/placa/
       obra_codigo). Se nao achar, registra erro e skip.
    5. UPSERT na tabela de documentos apropriada, matcheando por
       `onedrive_item_id`. Se existir e `last_modified` nao mudou,
       skip (docs_skipped). Se existir e mudou, update (docs_updated).
       Se nao existir, insert (docs_created).
    6. Grava `audit_log` com totals e finaliza o run.

Operacao e sincrona: para o MVP, roda dentro do request/task -- o
volume esperado e dezenas a centenas de arquivos por run. Escopos
maiores devem ser paginados/divididos via `scope` param no futuro.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.onedrive.client import (
    OneDriveClient,
    OneDriveError,
    OneDriveMockClient,
)
from app.modules.dp_sesmt.models import (
    DOC_EMP_TIPOS_VALIDOS,
    Employee,
    EmployeeDocument,
)
from app.modules.licitacoes.models import (
    DOC_EMPRESA_TIPOS_VALIDOS,
    EmpresaDocumento,
)
from app.modules.manutencao_frota.models import (
    TIPOS_DOC_VALIDOS as DOC_FROTA_TIPOS_VALIDOS,
)
from app.modules.manutencao_frota.models import (
    DocumentoVeiculo,
    Veiculo,
)
from app.modules.obras.models import (
    DOC_TIPOS_VALIDOS as DOC_OBRA_TIPOS_VALIDOS,
)
from app.modules.obras.models import (
    Obra,
    ObraDocumento,
)
from app.modules.onedrive_sync.models import (
    RUN_DONE,
    RUN_ERROR,
    SCOPE_ALL,
    SCOPE_DP,
    SCOPE_EMPRESA,
    SCOPE_FROTA,
    SCOPE_OBRAS,
    SOURCE_ONEDRIVE_SYNC,
    OneDriveSyncRun,
)
from app.modules.onedrive_sync.parser import (
    AREA_DP,
    AREA_EMPRESA,
    AREA_FROTA,
    AREA_OBRAS,
    ParsedMatch,
    parse_path,
)

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "onedrive_sync.run"

# Default de empresa_cnpj quando um doc e achado em `empresa/*` sem
# especificacao de CNPJ (a convencao e "1 empresa por tenant" no MVP).
# Quando houver multi-tenant por CNPJ, a convencao vira
# `empresa/<CNPJ>/<tipo>.pdf` e o parser ganha 1 braco.
_EMPRESA_DEFAULT_CNPJ = "empresa-default"


def _canonical_tipo(
    raw: str, tipos_validos: frozenset[str]
) -> str | None:
    """Devolve a forma canonica (como gravada no set) para `raw`.

    Lookup case-insensitive: a convencao de nomes por modulo nao e
    consistente (frota usa minusculas `crlv`/`ipva`, obras/dp/empresa
    usam MAIUSCULAS `CRLV`/`NR12`). Em vez de exigir do usuario que
    nomeie o arquivo do jeito certo pra cada pasta, normalizamos aqui.
    """
    lower = raw.lower()
    for canonical in tipos_validos:
        if canonical.lower() == lower:
            return canonical
    return None


@dataclass(slots=True)
class _Totals:
    files_scanned: int = 0
    docs_created: int = 0
    docs_updated: int = 0
    docs_skipped: int = 0
    errors: list[dict[str, str]] | None = None

    def add_error(self, path: str, reason: str) -> None:
        if self.errors is None:
            self.errors = []
        self.errors.append({"path": path, "reason": reason})


def _area_match(scope: str, area: str) -> bool:
    if scope == SCOPE_ALL:
        return True
    return (scope, area) in {
        (SCOPE_DP, AREA_DP),
        (SCOPE_FROTA, AREA_FROTA),
        (SCOPE_OBRAS, AREA_OBRAS),
        (SCOPE_EMPRESA, AREA_EMPRESA),
    }


def _parse_last_modified(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # Graph retorna ISO-8601 com "Z" ou "+00:00".
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    """Normaliza para comparacao cross-backend.

    SQLite (dev/tests) devolve datetime naive mesmo quando a coluna e
    `DateTime(timezone=True)`; Postgres (prod) devolve aware. Sem essa
    normalizacao, a comparacao `existing != new` sempre retorna True
    em SQLite (diferentes "kinds" de datetime) e o sync nunca skipa
    um arquivo ja sincronizado -- quebra idempotencia no dev.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


async def _load_employee_index(db: AsyncSession) -> dict[str, int]:
    """Mapa `entity_key` -> `employee_id`.

    Aceita tanto `id` (inteiro do banco) quanto `matricula` (str livre)
    como chave na pasta OneDrive -- o usuario que monta a pasta pode
    nao ter o id numerico em mente.
    """
    res = await db.execute(select(Employee.id, Employee.matricula))
    out: dict[str, int] = {}
    for emp_id, matricula in res.all():
        out[str(emp_id)] = emp_id
        if matricula:
            out[str(matricula).strip().upper()] = emp_id
    return out


async def _load_veiculo_index(db: AsyncSession) -> dict[str, int]:
    res = await db.execute(select(Veiculo.id, Veiculo.placa))
    out: dict[str, int] = {}
    for vid, placa in res.all():
        out[str(vid)] = vid
        if placa:
            out[str(placa).strip().upper().replace("-", "")] = vid
    return out


async def _load_obra_index(db: AsyncSession) -> dict[str, int]:
    res = await db.execute(select(Obra.id, Obra.codigo))
    out: dict[str, int] = {}
    for oid, codigo in res.all():
        out[str(oid)] = oid
        if codigo:
            out[str(codigo).strip().upper()] = oid
    return out


async def run_sync(
    db: AsyncSession,
    *,
    client: OneDriveClient | OneDriveMockClient,
    scope: str = SCOPE_ALL,
    actor: str | None = None,
) -> OneDriveSyncRun:
    """Top-level. Cria run, itera, commita, retorna run finalizado."""
    run = OneDriveSyncRun(
        scope=scope,
        triggered_by=actor,
        root_folder=client.root_folder,
    )
    db.add(run)
    await db.flush()  # preciso do run.id antes de associar docs
    totals = _Totals()
    try:
        items = await client.list_folder(relative_path="", recursive=True)
        employee_idx = await _load_employee_index(db)
        veiculo_idx = await _load_veiculo_index(db)
        obra_idx = await _load_obra_index(db)
        for item in items:
            totals.files_scanned += 1
            path = item.get("path") or ""
            parsed = parse_path(path)
            if parsed is None:
                totals.docs_skipped += 1
                continue
            if not _area_match(scope, parsed.area):
                totals.docs_skipped += 1
                continue
            try:
                await _apply_match(
                    db,
                    run=run,
                    item=item,
                    parsed=parsed,
                    employee_idx=employee_idx,
                    veiculo_idx=veiculo_idx,
                    obra_idx=obra_idx,
                    totals=totals,
                )
            except _SyncError as exc:
                totals.add_error(path, str(exc))
        run.status = RUN_DONE
        run.files_scanned = totals.files_scanned
        run.docs_created = totals.docs_created
        run.docs_updated = totals.docs_updated
        run.docs_skipped = totals.docs_skipped
        run.errors_count = len(totals.errors or [])
        run.summary_json = {
            "errors": totals.errors or [],
            "scope": scope,
        }
        run.finished_at = datetime.now(UTC)
        db.add(
            AuditLog(
                actor=actor or "system",
                action="sync",
                resource=_AUDIT_RESOURCE,
                resource_id=str(run.id),
                metadata_json=json.dumps(
                    {
                        "scope": scope,
                        "files_scanned": totals.files_scanned,
                        "docs_created": totals.docs_created,
                        "docs_updated": totals.docs_updated,
                        "docs_skipped": totals.docs_skipped,
                        "errors_count": len(totals.errors or []),
                    }
                ),
            )
        )
        await db.commit()
        await db.refresh(run)
        return run
    except OneDriveError as exc:
        run.status = RUN_ERROR
        run.error_message = f"OneDrive falhou: {exc}"
        run.finished_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(run)
        return run
    except Exception as exc:  # noqa: BLE001 -- queremos enterrar o run mesmo em bugs
        logger.exception("run_sync falhou inesperadamente")
        run.status = RUN_ERROR
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(run)
        return run


class _SyncError(Exception):
    """Erro de validacao recuperavel (1 arquivo) -- nao aborta o run."""


async def _apply_match(
    db: AsyncSession,
    *,
    run: OneDriveSyncRun,
    item: dict,
    parsed: ParsedMatch,
    employee_idx: dict[str, int],
    veiculo_idx: dict[str, int],
    obra_idx: dict[str, int],
    totals: _Totals,
) -> None:
    item_id = item.get("id")
    if not item_id:
        raise _SyncError("item sem id")
    item_path = item.get("path")
    last_modified = _parse_last_modified(item.get("last_modified"))

    if parsed.area == AREA_DP:
        emp_id = _lookup(employee_idx, parsed.entity_key)
        if emp_id is None:
            raise _SyncError(
                f"funcionario nao encontrado (chave={parsed.entity_key!r})"
            )
        tipo = _canonical_tipo(parsed.doc_tipo, DOC_EMP_TIPOS_VALIDOS)
        if tipo is None:
            raise _SyncError(
                f"doc_tipo {parsed.doc_tipo!r} nao e valido para DP"
            )
        await _upsert(
            db,
            model=EmployeeDocument,
            key_filter=EmployeeDocument.onedrive_item_id == item_id,
            run=run,
            item_id=item_id,
            item_path=item_path,
            last_modified=last_modified,
            totals=totals,
            defaults={"employee_id": emp_id, "tipo": tipo},
        )
    elif parsed.area == AREA_FROTA:
        veic_id = _lookup(veiculo_idx, parsed.entity_key)
        if veic_id is None:
            raise _SyncError(
                f"veiculo nao encontrado (chave={parsed.entity_key!r})"
            )
        tipo = _canonical_tipo(parsed.doc_tipo, DOC_FROTA_TIPOS_VALIDOS)
        if tipo is None:
            raise _SyncError(
                f"doc_tipo {parsed.doc_tipo!r} nao e valido para frota"
            )
        await _upsert(
            db,
            model=DocumentoVeiculo,
            key_filter=DocumentoVeiculo.onedrive_item_id == item_id,
            run=run,
            item_id=item_id,
            item_path=item_path,
            last_modified=last_modified,
            totals=totals,
            defaults={"veiculo_id": veic_id, "tipo": tipo},
        )
    elif parsed.area == AREA_OBRAS:
        obra_id = _lookup(obra_idx, parsed.entity_key)
        if obra_id is None:
            raise _SyncError(
                f"obra nao encontrada (chave={parsed.entity_key!r})"
            )
        tipo = _canonical_tipo(parsed.doc_tipo, DOC_OBRA_TIPOS_VALIDOS)
        if tipo is None:
            raise _SyncError(
                f"doc_tipo {parsed.doc_tipo!r} nao e valido para obras"
            )
        await _upsert(
            db,
            model=ObraDocumento,
            key_filter=ObraDocumento.onedrive_item_id == item_id,
            run=run,
            item_id=item_id,
            item_path=item_path,
            last_modified=last_modified,
            totals=totals,
            defaults={"obra_id": obra_id, "tipo": tipo},
        )
    elif parsed.area == AREA_EMPRESA:
        tipo = _canonical_tipo(parsed.doc_tipo, DOC_EMPRESA_TIPOS_VALIDOS)
        if tipo is None:
            raise _SyncError(
                f"doc_tipo {parsed.doc_tipo!r} nao e valido para empresa"
            )
        await _upsert(
            db,
            model=EmpresaDocumento,
            key_filter=EmpresaDocumento.onedrive_item_id == item_id,
            run=run,
            item_id=item_id,
            item_path=item_path,
            last_modified=last_modified,
            totals=totals,
            defaults={
                "empresa_cnpj": _EMPRESA_DEFAULT_CNPJ,
                "tipo": tipo,
            },
        )


def _lookup(idx: dict[str, int], raw_key: str | None) -> int | None:
    if raw_key is None:
        return None
    k = str(raw_key).strip()
    if not k:
        return None
    # Tenta 1) raw, 2) upper, 3) normalizacao de placa (sem hifen).
    for candidate in (k, k.upper(), k.upper().replace("-", "")):
        if candidate in idx:
            return idx[candidate]
    return None


async def _upsert(
    db: AsyncSession,
    *,
    model: type,
    key_filter,  # noqa: ANN001 -- SQLAlchemy binary expression
    run: OneDriveSyncRun,
    item_id: str,
    item_path: str | None,
    last_modified: datetime | None,
    totals: _Totals,
    defaults: dict,
) -> None:
    res = await db.execute(select(model).where(key_filter))
    existing = res.scalars().first()
    if existing is None:
        new = model(
            **defaults,
            source=SOURCE_ONEDRIVE_SYNC,
            onedrive_item_id=item_id,
            onedrive_path=item_path,
            onedrive_last_modified=last_modified,
            onedrive_sync_run_id=run.id,
        )
        db.add(new)
        totals.docs_created += 1
        return
    # Update se last_modified mudou OU se nunca teve last_modified.
    # SQLite devolve datetime naive mesmo pra coluna `DateTime(timezone=True)`,
    # entao comparo como UTC-naive para evitar inconsistencia entre
    # dev (SQLite naive) e prod (Postgres tz-aware).
    changed = (
        _to_utc_naive(existing.onedrive_last_modified)
        != _to_utc_naive(last_modified)
        or existing.onedrive_path != item_path
    )
    if not changed:
        totals.docs_skipped += 1
        return
    existing.onedrive_path = item_path
    existing.onedrive_last_modified = last_modified
    existing.onedrive_sync_run_id = run.id
    totals.docs_updated += 1



