"""Home/Comando Central — agregado de KPIs + integracoes + feed."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.diagnostico.models import DiagnosticoFinding, DiagnosticoRun
from app.modules.dp_sesmt.models import Afastamento, Employee
from app.modules.licitacoes.models import (
    CertidaoEmpresa,
    Edital,
    Licitacao,
    SavedQuery,
)
from app.modules.manutencao_frota.models import (
    ConsultaDetran,
    ParteDiaria,
    Veiculo,
)
from app.modules.onedrive_sync.models import OneDriveSyncRun

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


def _days_until(d: date | None, ref: date) -> int | None:
    if d is None:
        return None
    return (d - ref).days


@router.get("/home")
async def get_home(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Retorna todos os numeros e eventos da home.

    KPIs: conformidade, vencimentos, findings, veiculos, funcionarios,
    licitacoes + status de 8 integracoes + feed das ultimas automacoes.
    """
    today = date.today()
    horizon = today + timedelta(days=30)
    now = datetime.now(UTC)

    # -------- KPI: funcionarios --------
    total_emp = int(
        (await db.execute(select(func.count(Employee.id)))).scalar() or 0
    )
    ativos_emp = int(
        (
            await db.execute(
                select(func.count(Employee.id)).where(Employee.status == "ativo")
            )
        ).scalar()
        or 0
    )
    aso_vencido = int(
        (
            await db.execute(
                select(func.count(Employee.id)).where(
                    and_(
                        Employee.status == "ativo",
                        Employee.aso_validade.is_not(None),
                        Employee.aso_validade < today,
                    )
                )
            )
        ).scalar()
        or 0
    )
    aso_vencendo = int(
        (
            await db.execute(
                select(func.count(Employee.id)).where(
                    and_(
                        Employee.status == "ativo",
                        Employee.aso_validade.is_not(None),
                        Employee.aso_validade >= today,
                        Employee.aso_validade <= horizon,
                    )
                )
            )
        ).scalar()
        or 0
    )
    afastados = int(
        (
            await db.execute(
                select(func.count(Afastamento.id)).where(
                    Afastamento.data_retorno.is_(None)
                )
            )
        ).scalar()
        or 0
    )

    # -------- KPI: frota --------
    total_vei = int(
        (await db.execute(select(func.count(Veiculo.id)))).scalar() or 0
    )
    ativos_vei = int(
        (
            await db.execute(
                select(func.count(Veiculo.id)).where(Veiculo.status == "ativo")
            )
        ).scalar()
        or 0
    )
    partes_total = int(
        (await db.execute(select(func.count(ParteDiaria.id)))).scalar() or 0
    )
    partes_7d = int(
        (
            await db.execute(
                select(func.count(ParteDiaria.id)).where(
                    ParteDiaria.data >= today - timedelta(days=7)
                )
            )
        ).scalar()
        or 0
    )
    consultas_detran_30d = int(
        (
            await db.execute(
                select(func.count(ConsultaDetran.id)).where(
                    ConsultaDetran.executed_at
                    >= now - timedelta(days=30)
                )
            )
        ).scalar()
        or 0
    )

    # -------- KPI: licitacoes --------
    total_licit = int(
        (await db.execute(select(func.count(Licitacao.id)))).scalar() or 0
    )
    licit_7d = int(
        (
            await db.execute(
                select(func.count(Licitacao.id)).where(
                    Licitacao.created_at >= now - timedelta(days=7)
                )
            )
        ).scalar()
        or 0
    )
    total_editais = int(
        (await db.execute(select(func.count(Edital.id)))).scalar() or 0
    )
    total_queries = int(
        (await db.execute(select(func.count(SavedQuery.id)))).scalar() or 0
    )

    # -------- KPI: certidoes --------
    cert_vencidas = int(
        (
            await db.execute(
                select(func.count(CertidaoEmpresa.id)).where(
                    and_(
                        CertidaoEmpresa.validade.is_not(None),
                        CertidaoEmpresa.validade < today,
                    )
                )
            )
        ).scalar()
        or 0
    )
    cert_vencendo = int(
        (
            await db.execute(
                select(func.count(CertidaoEmpresa.id)).where(
                    and_(
                        CertidaoEmpresa.validade.is_not(None),
                        CertidaoEmpresa.validade >= today,
                        CertidaoEmpresa.validade <= horizon,
                    )
                )
            )
        ).scalar()
        or 0
    )
    cert_total = int(
        (await db.execute(select(func.count(CertidaoEmpresa.id)))).scalar() or 0
    )

    # -------- Diagnostico: ultima run --------
    last_run_row = (
        await db.execute(
            select(DiagnosticoRun)
            .order_by(DiagnosticoRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    conformidade_pct: float | None = None
    last_run: dict[str, Any] | None = None
    if last_run_row:
        total_f = last_run_row.total_findings or 0
        ok_f = last_run_row.ok_count or 0
        conformidade_pct = (ok_f / total_f * 100.0) if total_f else 0.0
        last_run = {
            "id": last_run_row.id,
            "started_at": last_run_row.started_at.isoformat()
            if last_run_row.started_at
            else None,
            "finished_at": last_run_row.finished_at.isoformat()
            if last_run_row.finished_at
            else None,
            "status": last_run_row.status,
            "total_findings": total_f,
            "ok_count": ok_f,
            "vencendo_count": last_run_row.vencendo_count or 0,
            "vencido_count": last_run_row.vencido_count or 0,
            "ausente_count": last_run_row.ausente_count or 0,
        }

    # Findings por area, ultima run
    findings_por_area: dict[str, dict[str, int]] = {}
    if last_run_row:
        area_rows = (
            await db.execute(
                select(
                    DiagnosticoFinding.area,
                    DiagnosticoFinding.status,
                    func.count(DiagnosticoFinding.id),
                )
                .where(DiagnosticoFinding.run_id == last_run_row.id)
                .group_by(DiagnosticoFinding.area, DiagnosticoFinding.status)
            )
        ).all()
        for area, st, cnt in area_rows:
            findings_por_area.setdefault(
                area, {"ok": 0, "vencendo": 0, "vencido": 0, "ausente": 0}
            )
            findings_por_area[area][st] = int(cnt)

    # -------- OneDrive Sync: ultima run --------
    last_od = (
        await db.execute(
            select(OneDriveSyncRun)
            .order_by(OneDriveSyncRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # -------- Integracoes status --------
    settings = get_settings()
    integrations: list[dict[str, Any]] = [
        {
            "key": "pncp",
            "label": "PNCP",
            "descr": "Crawler de licitações",
            "status": "ok",
            "last_event": f"{licit_7d} editais capturados (7d)",
            "configured": True,
        },
        {
            "key": "detran",
            "label": "Detran SP",
            "descr": "Consulta via Infosimples",
            "status": "ok" if consultas_detran_30d else "idle",
            "last_event": f"{consultas_detran_30d} consultas (30d)",
            "configured": bool(
                getattr(settings, "infosimples_api_token", "") or True
            ),
        },
        {
            "key": "crea",
            "label": "CREA",
            "descr": "ART / Acervo Técnico via Infosimples",
            "status": "ok",
            "last_event": "Pronto para importar",
            "configured": True,
        },
        {
            "key": "onedrive",
            "label": "OneDrive",
            "descr": "Sincronização de documentos (Microsoft Graph)",
            "status": "ok" if last_od else "idle",
            "last_event": (
                f"Última sync: {last_od.started_at.isoformat()} "
                f"({last_od.files_scanned or 0} arquivos)"
                if last_od
                else "Nunca sincronizado"
            ),
            "configured": True,
        },
        {
            "key": "documentai",
            "label": "Document AI",
            "descr": "OCR Google Cloud Document AI",
            "status": "ok" if partes_total else "idle",
            "last_event": f"{partes_total} partes diárias processadas",
            "configured": True,
        },
        {
            "key": "resend",
            "label": "Resend",
            "descr": "Alertas e boletins por email",
            "status": "ok",
            "last_event": f"{total_queries} queries de boletim ativas",
            "configured": True,
        },
        {
            "key": "dominio",
            "label": "Domínio / Onvio",
            "descr": "ERP folha de pagamento (Thomson Reuters)",
            "status": "pending",
            "last_event": "Aguardando credencial",
            "configured": False,
        },
        {
            "key": "totvs",
            "label": "TOTVS",
            "descr": "ERP Protheus — financeiro / contratos",
            "status": "pending",
            "last_event": "Aguardando migração",
            "configured": False,
        },
    ]

    # -------- Feed de automacoes (eventos recentes) --------
    feed: list[dict[str, Any]] = []

    # Ultimos runs de diagnostico (3)
    recent_runs = (
        (
            await db.execute(
                select(DiagnosticoRun)
                .order_by(DiagnosticoRun.started_at.desc())
                .limit(3)
            )
        )
        .scalars()
        .all()
    )
    for r in recent_runs:
        feed.append(
            {
                "ts": r.started_at.isoformat() if r.started_at else None,
                "kind": "diagnostico",
                "title": f"Diagnóstico Run #{r.id} — "
                f"{r.ok_count or 0}/{r.total_findings or 0} OK",
                "by": r.triggered_by or "system",
            }
        )

    # Ultimos onedrive syncs (3)
    recent_od = (
        (
            await db.execute(
                select(OneDriveSyncRun)
                .order_by(OneDriveSyncRun.started_at.desc())
                .limit(3)
            )
        )
        .scalars()
        .all()
    )
    for r in recent_od:
        feed.append(
            {
                "ts": r.started_at.isoformat() if r.started_at else None,
                "kind": "onedrive",
                "title": f"OneDrive sync — {r.files_scanned or 0} arquivos "
                f"({r.docs_created or 0} novos)",
                "by": r.triggered_by or "system",
            }
        )

    # Ultimas partes diarias (5)
    recent_partes = (
        (
            await db.execute(
                select(ParteDiaria).order_by(ParteDiaria.id.desc()).limit(5)
            )
        )
        .scalars()
        .all()
    )
    for p in recent_partes:
        feed.append(
            {
                "ts": p.created_at.isoformat() if p.created_at else None,
                "kind": "parte_diaria",
                "title": f"Parte Diária {p.data.isoformat() if p.data else '—'} · "
                f"OCR {p.ocr_status}",
                "by": getattr(p, "operador", None) or "system",
            }
        )

    # Ultimas consultas Detran (3)
    recent_detran = (
        (
            await db.execute(
                select(ConsultaDetran)
                .order_by(ConsultaDetran.executed_at.desc())
                .limit(3)
            )
        )
        .scalars()
        .all()
    )
    for c in recent_detran:
        feed.append(
            {
                "ts": c.executed_at.isoformat() if c.executed_at else None,
                "kind": "detran",
                "title": f"Detran {c.uf} · {c.placa or '—'} · {c.status}",
                "by": c.source or "system",
            }
        )

    feed.sort(key=lambda e: e.get("ts") or "", reverse=True)
    feed = feed[:10]

    return {
        "kpis": {
            "conformidade_pct": round(conformidade_pct, 1)
            if conformidade_pct is not None
            else None,
            "aso_vencido": aso_vencido,
            "aso_vencendo": aso_vencendo,
            "cert_vencida": cert_vencidas,
            "cert_vencendo": cert_vencendo,
            "cert_total": cert_total,
            "funcionarios_ativos": ativos_emp,
            "funcionarios_total": total_emp,
            "afastados": afastados,
            "veiculos_ativos": ativos_vei,
            "veiculos_total": total_vei,
            "partes_total": partes_total,
            "partes_7d": partes_7d,
            "licitacoes_total": total_licit,
            "licitacoes_7d": licit_7d,
            "editais_total": total_editais,
            "queries_boletim": total_queries,
            "consultas_detran_30d": consultas_detran_30d,
        },
        "last_diagnostico_run": last_run,
        "findings_por_area": findings_por_area,
        "integrations": integrations,
        "feed": feed,
    }
