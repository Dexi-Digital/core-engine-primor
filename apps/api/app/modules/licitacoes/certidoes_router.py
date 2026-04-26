"""HTTP router for D.6 (CNDs / atestados).

Mounted under `/api/v1/licitacoes/certidoes` -- a separate APIRouter
(instead of inline in `licitacoes.router`) to dodge the ambiguity with
the catch-all `GET /licitacoes/{licitacao_id}` route that would treat
`certidoes` as an int and return 422.
"""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.integrations.resend.client import ResendClient
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.licitacoes.certidoes import (
    TIPOS_VALIDOS,
    compute_status,
    create_certidao,
    delete_certidao,
    dispatch_expiration_alerts,
    get_certidao,
    list_certidoes,
    update_certidao,
)
from app.modules.licitacoes.schemas import (
    CertidaoAlertaDispatchPayload,
    CertidaoAlertaSummary,
    CertidaoCreate,
    CertidaoRead,
    CertidaoUpdate,
)

router = APIRouter()


def _certidao_to_read(certidao, *, today: _date | None = None) -> CertidaoRead:
    """Hidrata a view com `status_atual` + `dias_para_vencer`.

    Os dois campos sao puramente computados a partir da `validade` --
    ficam fora do banco para evitar inconsistencia (a data avanca todo
    dia, recalcular sob demanda e mais simples que rodar cron de update).
    """
    today = today or _date.today()
    payload = CertidaoRead.model_validate(certidao)
    payload.status_atual = compute_status(certidao.validade, today=today)
    if certidao.validade is not None:
        payload.dias_para_vencer = (certidao.validade - today).days
    return payload


@router.get("", response_model=list[CertidaoRead])
async def list_certidoes_endpoint(
    empresa_cnpj: str | None = Query(None, max_length=32),
    tipo: str | None = Query(None, max_length=64),
    status: str | None = Query(
        None,
        description=(
            "Filtra por status computado: vigente | vencendo | vencido | sem_validade"
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> list[CertidaoRead]:
    rows = await list_certidoes(
        db, empresa_cnpj=empresa_cnpj, tipo=tipo, status=status
    )
    today = _date.today()
    return [_certidao_to_read(r, today=today) for r in rows]


@router.post("", response_model=CertidaoRead, status_code=201)
async def create_certidao_endpoint(
    payload: CertidaoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CertidaoRead:
    if payload.tipo not in TIPOS_VALIDOS:
        # Aceitamos string livre para "OUTRO/custom"; 422 seria mais
        # purista mas atrapalha a UI. O service ja loga.
        pass
    row = await create_certidao(
        db,
        empresa_cnpj=payload.empresa_cnpj,
        tipo=payload.tipo,
        numero=payload.numero,
        emissao=payload.emissao,
        validade=payload.validade,
        arquivo_path=payload.arquivo_path,
        orgao_emissor=payload.orgao_emissor,
        observacoes=payload.observacoes,
        actor=current_user.email,
    )
    return _certidao_to_read(row)


@router.post("/dispatch-alerts", response_model=CertidaoAlertaSummary)
async def dispatch_certidao_alerts_endpoint(
    payload: CertidaoAlertaDispatchPayload,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> CertidaoAlertaSummary:
    """On-demand dispatch dos alertas de vencimento. Normalmente rodado
    pelo Celery beat 1x/dia.

    Requires RESEND_API_KEY; returns 503 if not configured.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(
            status_code=503,
            detail="RESEND_API_KEY nao configurada; configure em settings.",
        )
    resend = ResendClient(api_key=settings.resend_api_key)
    try:
        summary = await dispatch_expiration_alerts(
            db,
            resend,
            recipients=[str(r) for r in payload.recipients],
        )
    finally:
        await resend.aclose()
    return CertidaoAlertaSummary(
        total_certidoes=summary.total_certidoes,
        sent=summary.sent,
        skipped=summary.skipped,
        failed=summary.failed,
        results=[
            {
                "certidao_id": r.certidao_id,
                "janela": r.janela,
                "status": r.status,
                "recipients": r.recipients,
                "resend_message_id": r.resend_message_id,
                "error_message": r.error_message,
            }
            for r in summary.results
        ],  # type: ignore[arg-type]
    )


@router.get("/{certidao_id}", response_model=CertidaoRead)
async def get_certidao_endpoint(
    certidao_id: int,
    db: AsyncSession = Depends(get_db),
) -> CertidaoRead:
    row = await get_certidao(db, certidao_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Certidao nao encontrada")
    return _certidao_to_read(row)


@router.put("/{certidao_id}", response_model=CertidaoRead)
async def update_certidao_endpoint(
    certidao_id: int,
    payload: CertidaoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CertidaoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await update_certidao(
        db, certidao_id, actor=current_user.email, **fields
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Certidao nao encontrada")
    return _certidao_to_read(row)


@router.delete("/{certidao_id}", status_code=204)
async def delete_certidao_endpoint(
    certidao_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_certidao(db, certidao_id, actor=current_user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Certidao nao encontrada")
