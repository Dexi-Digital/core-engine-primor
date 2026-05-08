"""Servico do diagnostico de pastas OneDrive.

Orquestra:
  1. walk recursivo em `{root_folder}` via Graph API;
  2. agrupa items por area/entity_key seguindo o padrao do parser
     `onedrive_sync.parser.parse_path`;
  3. carrega indice de entidades do DB (funcionarios, veiculos, obras);
  4. cruza os dois e emite `Finding`s tipados:
        FALTANDO          -- doc obrigatorio nao existe no OneDrive
        EXTRA             -- arquivo com doc_tipo nao reconhecido
        FORA_DO_PADRAO    -- path nao casa com a convencao
        ENTIDADE_FANTASMA -- pasta de entidade sem registro no DB
        ENTIDADE_SEM_PASTA-- entidade no DB sem pasta no OneDrive

Design: sem persistir em DB (POC). Roda sob demanda e devolve JSON.
Fase 2 salva snapshots em tabela pra historico/tendencia.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt.models import Employee
from app.modules.manutencao_frota.models import Veiculo
from app.modules.obras.models import Obra
from app.modules.onedrive_diagnostico.spec import (
    AREA_DP,
    AREA_EMPRESA,
    AREA_FROTA,
    AREA_OBRAS,
    AREAS,
    SPEC,
    AreaSpec,
)
from app.modules.onedrive_sync.parser import (
    AREA_DP as PARSER_AREA_DP,
)
from app.modules.onedrive_sync.parser import (
    AREA_EMPRESA as PARSER_AREA_EMPRESA,
)
from app.modules.onedrive_sync.parser import (
    AREA_FROTA as PARSER_AREA_FROTA,
)
from app.modules.onedrive_sync.parser import (
    AREA_OBRAS as PARSER_AREA_OBRAS,
)
from app.modules.onedrive_sync.parser import (
    AREAS_VALIDAS,
)

logger = logging.getLogger(__name__)

# --- tipos de finding ------------------------------------------------------

FINDING_FALTANDO = "faltando"
FINDING_EXTRA = "extra"
FINDING_FORA_DO_PADRAO = "fora_do_padrao"
FINDING_ENTIDADE_FANTASMA = "entidade_fantasma"
FINDING_ENTIDADE_SEM_PASTA = "entidade_sem_pasta"
FINDING_OK = "ok"

FINDING_TYPES = (
    FINDING_FALTANDO,
    FINDING_EXTRA,
    FINDING_FORA_DO_PADRAO,
    FINDING_ENTIDADE_FANTASMA,
    FINDING_ENTIDADE_SEM_PASTA,
    FINDING_OK,
)

# Espelha parser.AREAS_VALIDAS pra nao depender implicitamente:
assert frozenset(
    {PARSER_AREA_DP, PARSER_AREA_FROTA, PARSER_AREA_OBRAS, PARSER_AREA_EMPRESA}
) == AREAS_VALIDAS


@dataclass(slots=True)
class Finding:
    kind: str
    area: str
    entity_key: str | None  # matricula/placa/codigo/None (empresa)
    entity_label: str | None  # nome humano, quando disponivel
    doc_tipo: str | None  # e.g. ASO, CRLV, etc -- None pra fantasma/sem pasta
    path: str | None  # relativo ao root_folder
    message: str


@dataclass(slots=True)
class EntityReport:
    area: str
    entity_key: str  # "dp/42", "frota/ABC1234", etc; "empresa" singleton
    label: str
    findings: list[Finding] = field(default_factory=list)
    ok: bool = False  # True se sem findings de gravidade (FALTANDO/FORA/EXTRA)


@dataclass(slots=True)
class DiagnosticoResult:
    root_folder: str
    total_files: int
    by_area: dict[str, list[EntityReport]]
    counts: dict[str, int]  # {finding_kind: n}


# --- index loaders ---------------------------------------------------------


async def _load_employees(db: AsyncSession) -> dict[str, tuple[Employee, str]]:
    """Mapa de *entity_key candidatas* -> (employee, label).

    Igual a `_load_employee_index` do sync, mas guarda o objeto
    completo pra gerar label nos findings.
    """
    res = await db.execute(
        select(Employee.id, Employee.matricula, Employee.nome_completo, Employee.cpf)
    )
    out: dict[str, tuple[int, str, str, str | None]] = {}
    for eid, matricula, nome, cpf in res.all():
        out[str(eid)] = (eid, matricula or "", nome or "", cpf)
        if matricula:
            out[str(matricula).strip().upper()] = (eid, matricula, nome or "", cpf)
    return out  # type: ignore[return-value]


async def _load_veiculos(
    db: AsyncSession,
) -> dict[str, tuple[int, str, str | None, str | None]]:
    res = await db.execute(
        select(Veiculo.id, Veiculo.placa, Veiculo.marca, Veiculo.modelo)
    )
    out: dict[str, tuple[int, str, str | None, str | None]] = {}
    for vid, placa, marca, modelo in res.all():
        out[str(vid)] = (vid, placa or "", marca, modelo)
        if placa:
            normalized = str(placa).strip().upper().replace("-", "")
            out[normalized] = (vid, placa, marca, modelo)
    return out


async def _load_obras(db: AsyncSession) -> dict[str, tuple[int, str, str]]:
    res = await db.execute(select(Obra.id, Obra.codigo, Obra.nome))
    out: dict[str, tuple[int, str, str]] = {}
    for oid, codigo, nome in res.all():
        out[str(oid)] = (oid, codigo or "", nome or "")
        if codigo:
            base = str(codigo).strip().upper()
            out[base] = (oid, codigo, nome or "")
            # tolerancia a pastas com/sem hifen (mesma estrategia da frota)
            out[base.replace("-", "")] = (oid, codigo, nome or "")
    return out


# --- core ------------------------------------------------------------------


def _ext_stem(filename: str) -> str | None:
    """`NR12.pdf` -> `NR12`; `a b.pdf` -> `A_B`; no ext -> None."""
    if "." not in filename:
        return None
    stem = filename.rsplit(".", 1)[0].strip()
    if not stem:
        return None
    return "_".join(stem.upper().split())


def _canonical(tipo_raw: str, spec: AreaSpec) -> str | None:
    """Case-insensitive match contra spec.known."""
    lower = tipo_raw.lower()
    for canonical in spec.known:
        if canonical.lower() == lower:
            return canonical
    return None


def _classify_path(path: str) -> tuple[str | None, str | None, str | None, str]:
    """Classifica um path relativo em (area, entity_key, doc_tipo, reason).

    Retorna (None, None, None, reason) se fora do padrao.
    `entity_key` fica None pra empresa (singleton).
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None, None, None, f"path com depth {len(parts)} (esperado 2 ou 3)"
    area = parts[0].lower()
    if area not in AREAS_VALIDAS:
        return (
            None,
            None,
            None,
            f"primeiro segmento {parts[0]!r} nao e area conhecida",
        )
    if area == PARSER_AREA_EMPRESA:
        if len(parts) != 2:
            return (
                None,
                None,
                None,
                "pasta empresa/ deve conter arquivos diretos (sem subpastas)",
            )
        tipo = _ext_stem(parts[1])
        return area, None, tipo, "ok"
    # dp/<key>/<file>, frota/<key>/<file>, obras/<key>/<file>
    if len(parts) != 3:
        return (
            None,
            None,
            None,
            f"pasta {area}/ deve conter <entity_key>/<arquivo> (depth 3)",
        )
    tipo = _ext_stem(parts[2])
    return area, parts[1], tipo, "ok"


def _make_entity_report(
    area: str, entity_key: str, label: str
) -> EntityReport:
    return EntityReport(area=area, entity_key=entity_key, label=label)


def _compute_findings(
    items: list[dict[str, Any]],
    *,
    employees: dict[str, tuple[int, str, str, str | None]],
    veiculos: dict[str, tuple[int, str, str | None, str | None]],
    obras: dict[str, tuple[int, str, str]],
) -> DiagnosticoResult:
    """Core puro (nao I/O). Testavel isolado do Graph + DB.

    `items` sao os dicts de `OneDriveClient.list_folder()` --
    { id, name, path, size, last_modified }, path relativo ao root.
    """

    # entidades-pasta vistas no OneDrive: set de (area, entity_key_lookup)
    seen_entities: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    # mapeia (area, key) -> lista de arquivos encontrados (para reports)
    files_by_entity: dict[
        tuple[str, str | None], list[tuple[str, str | None, str]]
    ] = defaultdict(list)
    fora_do_padrao: list[Finding] = []

    for it in items:
        raw_path: str = it.get("path") or ""
        area, key, tipo, reason = _classify_path(raw_path)
        if area is None:
            fora_do_padrao.append(
                Finding(
                    kind=FINDING_FORA_DO_PADRAO,
                    area=_guess_area_from_path(raw_path),
                    entity_key=None,
                    entity_label=None,
                    doc_tipo=None,
                    path=raw_path,
                    message=reason,
                )
            )
            continue
        # empresa e singleton: usamos chave lookup "empresa"
        key_lookup = key.upper().replace("-", "") if key else None
        seen_entities[(area, key_lookup)].add(tipo or "")
        files_by_entity[(area, key_lookup)].append(
            (tipo or "?", tipo, raw_path)
        )

    reports_by_area: dict[str, list[EntityReport]] = {a: [] for a in AREAS}
    counts: dict[str, int] = {k: 0 for k in FINDING_TYPES}

    # Para areas multi-entidade: expandir entidades do DB, comparar
    # com seen_entities.
    _process_dp(
        employees=employees,
        files_by_entity=files_by_entity,
        reports=reports_by_area[AREA_DP],
        counts=counts,
    )
    _process_frota(
        veiculos=veiculos,
        files_by_entity=files_by_entity,
        reports=reports_by_area[AREA_FROTA],
        counts=counts,
    )
    _process_obras(
        obras=obras,
        files_by_entity=files_by_entity,
        reports=reports_by_area[AREA_OBRAS],
        counts=counts,
    )
    _process_empresa(
        files_by_entity=files_by_entity,
        reports=reports_by_area[AREA_EMPRESA],
        counts=counts,
    )

    # fora-do-padrao: acumulado como pseudo-report por area detectada
    if fora_do_padrao:
        counts[FINDING_FORA_DO_PADRAO] += len(fora_do_padrao)
        # agrupa por area detectada (ou "desconhecido")
        grouped: dict[str, list[Finding]] = defaultdict(list)
        for f in fora_do_padrao:
            grouped[f.area or "desconhecido"].append(f)
        for area_guess, fs in grouped.items():
            bucket = reports_by_area.setdefault(area_guess, [])
            bucket.append(
                EntityReport(
                    area=area_guess,
                    entity_key="__fora_do_padrao__",
                    label="(arquivos fora do padrao)",
                    findings=fs,
                    ok=False,
                )
            )

    return DiagnosticoResult(
        root_folder="",  # preenchido pelo caller
        total_files=len(items),
        by_area=reports_by_area,
        counts=counts,
    )


def _guess_area_from_path(path: str) -> str:
    first = path.strip("/").split("/", 1)[0].lower() if path else ""
    if first in AREAS:
        return first
    return "desconhecido"


def _process_dp(
    *,
    employees: dict[str, tuple[int, str, str, str | None]],
    files_by_entity: dict[
        tuple[str, str | None], list[tuple[str, str | None, str]]
    ],
    reports: list[EntityReport],
    counts: dict[str, int],
) -> None:
    spec = SPEC[AREA_DP]
    # 1) para cada pasta dp/ vista no OneDrive, existe entidade?
    seen_keys = {
        k for (a, k) in files_by_entity if a == AREA_DP and k is not None
    }
    # Index de keys conhecidas: ids stringfied + matriculas uppercased
    known_keys = set(employees.keys())
    # 2) emite fantasma pra keys que nao batem
    for key in sorted(seen_keys):
        report = EntityReport(
            area=AREA_DP, entity_key=key, label=key, findings=[]
        )
        if key not in known_keys:
            report.findings.append(
                Finding(
                    kind=FINDING_ENTIDADE_FANTASMA,
                    area=AREA_DP,
                    entity_key=key,
                    entity_label=None,
                    doc_tipo=None,
                    path=f"dp/{key}",
                    message=f"pasta dp/{key} nao bate com nenhum funcionario no DB",
                )
            )
            counts[FINDING_ENTIDADE_FANTASMA] += 1
            reports.append(report)
            continue
        _, matricula, nome, _cpf = employees[key]
        report.label = f"{nome} ({matricula or '#' + key})" if nome else key
        _apply_spec_diff(
            spec=spec,
            entity_key=key,
            entity_label=report.label,
            files=files_by_entity[(AREA_DP, key)],
            report=report,
            counts=counts,
        )
        reports.append(report)
    # 3) entidades no DB sem pasta
    # De-dupla: uma entidade pode aparecer em `employees` 2x (por id e matricula).
    # Agrupa por employee.id real pra nao reportar 2x.
    seen_eids: set[int] = set()
    for _key, (eid, matricula, nome, _cpf) in employees.items():
        if eid in seen_eids:
            continue
        seen_eids.add(eid)
        # Considera "tem pasta" se qualquer key conhecida dessa entidade
        # apareceu em `seen_keys`.
        aliases = {k for k, (e, *_rest) in employees.items() if e == eid}
        if aliases & seen_keys:
            continue
        label = f"{nome} ({matricula or '#' + str(eid)})" if nome else f"#{eid}"
        report = EntityReport(
            area=AREA_DP,
            entity_key=str(eid),
            label=label,
            findings=[
                Finding(
                    kind=FINDING_ENTIDADE_SEM_PASTA,
                    area=AREA_DP,
                    entity_key=str(eid),
                    entity_label=label,
                    doc_tipo=None,
                    path=f"dp/{matricula or eid}",
                    message=f"funcionario {label} nao tem pasta no OneDrive",
                )
            ],
        )
        counts[FINDING_ENTIDADE_SEM_PASTA] += 1
        reports.append(report)


def _process_frota(
    *,
    veiculos: dict[str, tuple[int, str, str | None, str | None]],
    files_by_entity: dict[
        tuple[str, str | None], list[tuple[str, str | None, str]]
    ],
    reports: list[EntityReport],
    counts: dict[str, int],
) -> None:
    spec = SPEC[AREA_FROTA]
    seen_keys = {
        k for (a, k) in files_by_entity if a == AREA_FROTA and k is not None
    }
    known_keys = set(veiculos.keys())
    for key in sorted(seen_keys):
        report = EntityReport(
            area=AREA_FROTA, entity_key=key, label=key, findings=[]
        )
        if key not in known_keys:
            report.findings.append(
                Finding(
                    kind=FINDING_ENTIDADE_FANTASMA,
                    area=AREA_FROTA,
                    entity_key=key,
                    entity_label=None,
                    doc_tipo=None,
                    path=f"frota/{key}",
                    message=f"pasta frota/{key} nao bate com nenhum veiculo no DB",
                )
            )
            counts[FINDING_ENTIDADE_FANTASMA] += 1
            reports.append(report)
            continue
        _, placa, marca, modelo = veiculos[key]
        bits = " ".join(b for b in (marca, modelo) if b)
        report.label = f"{placa} ({bits})".strip() if bits else placa or key
        _apply_spec_diff(
            spec=spec,
            entity_key=key,
            entity_label=report.label,
            files=files_by_entity[(AREA_FROTA, key)],
            report=report,
            counts=counts,
        )
        reports.append(report)
    seen_vids: set[int] = set()
    for _key, (vid, placa, marca, modelo) in veiculos.items():
        if vid in seen_vids:
            continue
        seen_vids.add(vid)
        aliases = {k for k, (v, *_rest) in veiculos.items() if v == vid}
        if aliases & seen_keys:
            continue
        bits = " ".join(b for b in (marca, modelo) if b)
        label = f"{placa} ({bits})".strip() if bits else placa or f"#{vid}"
        report = EntityReport(
            area=AREA_FROTA,
            entity_key=str(vid),
            label=label,
            findings=[
                Finding(
                    kind=FINDING_ENTIDADE_SEM_PASTA,
                    area=AREA_FROTA,
                    entity_key=str(vid),
                    entity_label=label,
                    doc_tipo=None,
                    path=f"frota/{placa or vid}",
                    message=f"veiculo {label} nao tem pasta no OneDrive",
                )
            ],
        )
        counts[FINDING_ENTIDADE_SEM_PASTA] += 1
        reports.append(report)


def _process_obras(
    *,
    obras: dict[str, tuple[int, str, str]],
    files_by_entity: dict[
        tuple[str, str | None], list[tuple[str, str | None, str]]
    ],
    reports: list[EntityReport],
    counts: dict[str, int],
) -> None:
    spec = SPEC[AREA_OBRAS]
    seen_keys = {
        k for (a, k) in files_by_entity if a == AREA_OBRAS and k is not None
    }
    known_keys = set(obras.keys())
    for key in sorted(seen_keys):
        report = EntityReport(
            area=AREA_OBRAS, entity_key=key, label=key, findings=[]
        )
        if key not in known_keys:
            report.findings.append(
                Finding(
                    kind=FINDING_ENTIDADE_FANTASMA,
                    area=AREA_OBRAS,
                    entity_key=key,
                    entity_label=None,
                    doc_tipo=None,
                    path=f"obras/{key}",
                    message=f"pasta obras/{key} nao bate com nenhuma obra no DB",
                )
            )
            counts[FINDING_ENTIDADE_FANTASMA] += 1
            reports.append(report)
            continue
        _, codigo, nome = obras[key]
        report.label = f"{codigo} - {nome}" if nome else codigo or key
        _apply_spec_diff(
            spec=spec,
            entity_key=key,
            entity_label=report.label,
            files=files_by_entity[(AREA_OBRAS, key)],
            report=report,
            counts=counts,
        )
        reports.append(report)
    seen_oids: set[int] = set()
    for _key, (oid, codigo, nome) in obras.items():
        if oid in seen_oids:
            continue
        seen_oids.add(oid)
        aliases = {k for k, (o, *_rest) in obras.items() if o == oid}
        if aliases & seen_keys:
            continue
        label = f"{codigo} - {nome}" if nome else codigo or f"#{oid}"
        report = EntityReport(
            area=AREA_OBRAS,
            entity_key=str(oid),
            label=label,
            findings=[
                Finding(
                    kind=FINDING_ENTIDADE_SEM_PASTA,
                    area=AREA_OBRAS,
                    entity_key=str(oid),
                    entity_label=label,
                    doc_tipo=None,
                    path=f"obras/{codigo or oid}",
                    message=f"obra {label} nao tem pasta no OneDrive",
                )
            ],
        )
        counts[FINDING_ENTIDADE_SEM_PASTA] += 1
        reports.append(report)


def _process_empresa(
    *,
    files_by_entity: dict[
        tuple[str, str | None], list[tuple[str, str | None, str]]
    ],
    reports: list[EntityReport],
    counts: dict[str, int],
) -> None:
    spec = SPEC[AREA_EMPRESA]
    # empresa e singleton -- (AREA_EMPRESA, None)
    files = files_by_entity.get((AREA_EMPRESA, None), [])
    report = EntityReport(
        area=AREA_EMPRESA,
        entity_key="empresa",
        label="Empresa (singleton)",
        findings=[],
    )
    if not files:
        # nao e fatal -- so reporta "sem pasta"
        report.findings.append(
            Finding(
                kind=FINDING_ENTIDADE_SEM_PASTA,
                area=AREA_EMPRESA,
                entity_key="empresa",
                entity_label="Empresa",
                doc_tipo=None,
                path="empresa/",
                message="pasta empresa/ esta vazia ou nao existe",
            )
        )
        counts[FINDING_ENTIDADE_SEM_PASTA] += 1
        reports.append(report)
        return
    _apply_spec_diff(
        spec=spec,
        entity_key="empresa",
        entity_label="Empresa",
        files=files,
        report=report,
        counts=counts,
    )
    reports.append(report)


def _apply_spec_diff(
    *,
    spec: AreaSpec,
    entity_key: str,
    entity_label: str,
    files: list[tuple[str, str | None, str]],
    report: EntityReport,
    counts: dict[str, int],
) -> None:
    """Compara docs esperados vs encontrados pra UMA entidade.

    `files` = [(tipo_stem_raw, tipo_stem_original, path), ...].
    """
    present_canonical: set[str] = set()
    extras: list[Finding] = []
    oks: list[Finding] = []
    for tipo_stem, _orig, path in files:
        if not tipo_stem:
            continue
        canonical = _canonical(tipo_stem, spec)
        if canonical is None:
            extras.append(
                Finding(
                    kind=FINDING_EXTRA,
                    area=spec.area,
                    entity_key=entity_key,
                    entity_label=entity_label,
                    doc_tipo=tipo_stem,
                    path=path,
                    message=(
                        f"arquivo {path} com doc_tipo {tipo_stem!r} nao faz "
                        f"parte dos tipos conhecidos da area {spec.area}"
                    ),
                )
            )
            counts[FINDING_EXTRA] += 1
        else:
            present_canonical.add(canonical)
            oks.append(
                Finding(
                    kind=FINDING_OK,
                    area=spec.area,
                    entity_key=entity_key,
                    entity_label=entity_label,
                    doc_tipo=canonical,
                    path=path,
                    message="ok",
                )
            )
            counts[FINDING_OK] += 1
    # faltando: required - present
    for tipo in sorted(spec.required):
        if tipo in present_canonical:
            continue
        report.findings.append(
            Finding(
                kind=FINDING_FALTANDO,
                area=spec.area,
                entity_key=entity_key,
                entity_label=entity_label,
                doc_tipo=tipo,
                path=None,
                message=f"{tipo} obrigatorio nao encontrado no OneDrive",
            )
        )
        counts[FINDING_FALTANDO] += 1
    # OK deliberadamente nao entram na lista final (poluicao).
    # Se quiser mostrar na UI, expor via flag.
    report.findings.extend(extras)
    # ok = sem findings de gravidade
    grav = any(
        f.kind
        in (
            FINDING_FALTANDO,
            FINDING_EXTRA,
            FINDING_FORA_DO_PADRAO,
            FINDING_ENTIDADE_FANTASMA,
            FINDING_ENTIDADE_SEM_PASTA,
        )
        for f in report.findings
    )
    report.ok = not grav


# --- entry point -----------------------------------------------------------


async def run_diagnostico(
    db: AsyncSession,
    *,
    client: Any,  # OneDriveClient | OneDriveMockClient (evita circular)
    area: str = "all",
) -> DiagnosticoResult:
    """Roda o diagnostico completo (ou filtrado por area).

    `area='all'` escaneia tudo; caso contrario, filtra em memoria.
    Nao vale a pena limitar o walk via Graph porque a arvore ja vem
    paginada (50 items por request).
    """
    items = await client.list_folder(relative_path="", recursive=True)
    employees = await _load_employees(db)
    veiculos = await _load_veiculos(db)
    obras = await _load_obras(db)

    result = _compute_findings(
        items,
        employees=employees,
        veiculos=veiculos,
        obras=obras,
    )
    result.root_folder = getattr(client, "root_folder", "")

    if area != "all":
        if area not in AREAS:
            raise ValueError(
                f"area invalida: {area!r} -- validas: all, {', '.join(AREAS)}"
            )
        result.by_area = {area: result.by_area.get(area, [])}

    return result
