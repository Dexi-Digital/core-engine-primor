"""Modulo C - Financeiro e Contratos.

Rotas do ciclo de contratos (Squad 5, demanda #12) sob
`/api/v1/financeiro/contratos`. Assinatura digital e integracao
EasyJur real ficam fora (ver models.py).

Ordem de rotas: estaticas (`/contratos/dispatch-alerts`, Task 5) ANTES
de `/contratos/{contrato_id}` -- mesmo racional do D.6.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date as _date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.integrations.onedrive.client import build_onedrive_client
from app.integrations.onedrive.storage import OneDriveStorage
from app.integrations.resend.client import ResendClient
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.dp_sesmt.schemas import ModuleStatus
from app.modules.financeiro_contratos.alerts import dispatch_contrato_alerts
from app.modules.financeiro_contratos.schemas import (
    ContratoAlertaDispatchPayload,
    ContratoAlertaSummary,
    ContratoCreate,
    ContratoRead,
    ContratoUpdate,
)
from app.modules.financeiro_contratos.service import (
    compute_vencimento_status,
    create_contrato,
    delete_contrato,
    get_contrato,
    list_contratos,
    set_arquivo_contrato,
    update_contrato,
)
from app.modules.licitacoes.storage import EditaisStorage, LocalStorage

router = APIRouter()

# Cap de tamanho do PDF de contrato (module-level para permitir
# monkeypatch em teste). O `arquivo.read()` ainda bufferiza o corpo
# inteiro em memoria antes da checagem -- o cap evita persistir no
# storage, nao evita o buffer em si (proteção real de tamanho de
# request fica a cargo do proxy/gateway em producao).
MAX_CONTRATO_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB


async def get_contratos_storage() -> AsyncIterator[EditaisStorage]:
    """Storage dedicado para PDFs de contrato.

    Mesmo backend do storage de editais, raiz `contratos_storage_subdir`
    separada. Async generator para garantir aclose() do client OneDrive
    no fim da request (padrao get_fiscal_storage).
    """
    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.contratos_storage_subdir,
        )
        try:
            yield OneDriveStorage(client)
        finally:
            await client.aclose()
        return
    base = Path(settings.editais_storage_path).parent
    yield LocalStorage(base / settings.contratos_storage_subdir)


def _contrato_to_read(contrato, *, today: _date | None = None) -> ContratoRead:
    """Hidrata a view com os computados de vencimento (padrao D.6)."""
    today = today or _date.today()
    payload = ContratoRead.model_validate(contrato)
    payload.vencimento_status = compute_vencimento_status(
        contrato.data_fim, today=today
    )
    if contrato.data_fim is not None:
        payload.dias_para_vencer = (contrato.data_fim - today).days
    return payload


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="financeiro_contratos", implemented=True)


@router.get("/contratos", response_model=list[ContratoRead])
async def list_contratos_endpoint(
    status: str | None = Query(None, max_length=32),
    tipo: str | None = Query(None, max_length=32),
    obra_id: int | None = Query(None),
    vence_em_dias: int | None = Query(
        None, ge=0, le=365,
        description="Somente contratos com data_fim entre hoje e hoje+N dias",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[ContratoRead]:
    rows = await list_contratos(
        db, status=status, tipo=tipo, obra_id=obra_id, vence_em_dias=vence_em_dias
    )
    today = _date.today()
    return [_contrato_to_read(r, today=today) for r in rows]


@router.post("/contratos", response_model=ContratoRead, status_code=201)
async def create_contrato_endpoint(
    payload: ContratoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    try:
        row = await create_contrato(
            db,
            titulo=payload.titulo,
            contraparte_nome=payload.contraparte_nome,
            contraparte_documento=payload.contraparte_documento,
            tipo=payload.tipo,
            obra_id=payload.obra_id,
            valor=payload.valor,
            data_inicio=payload.data_inicio,
            data_fim=payload.data_fim,
            status=payload.status,
            easyjur_ref=payload.easyjur_ref,
            observacoes=payload.observacoes,
            actor=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _contrato_to_read(row)


@router.post("/contratos/dispatch-alerts", response_model=ContratoAlertaSummary)
async def dispatch_contrato_alerts_endpoint(
    payload: ContratoAlertaDispatchPayload,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ContratoAlertaSummary:
    """Disparo manual dos alertas (a demo Vercel nao tem worker/beat).
    Requires RESEND_API_KEY; 503 se ausente -- paridade com o D.6.

    ATENCAO -- colisao com o beat: o dispatch manual grava as MESMAS
    janelas em `contratos_alertas_log` que o job diario do Celery Beat
    (`contrato-alerts-daily`, 08h15). Chamar este endpoint marca a
    janela como enviada e SUPRIME o alerta do beat para os mesmos
    contratos/janelas naquele dia (idempotencia por
    `uq_contrato_alerta_janela`, sem distincao de origem/`source`).
    NAO e uma ferramenta de teste/dry-run -- qualquer chamada dispara
    email de verdade para os `recipients` informados. Use apenas
    emails reais de producao; disparos de teste vao "queimar" a janela
    e o contrato correspondente nao recebera o alerta automatico do
    beat. (Separar por `source` na unique key para permitir reenvio
    manual sem suprimir o beat fica em ticket a parte.)

    ROUTE-ORDERING: precisa estar registrada ANTES de
    `/contratos/{contrato_id}` (abaixo) -- senao FastAPI tentaria
    converter "dispatch-alerts" para int e devolveria 422.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise HTTPException(
            status_code=503,
            detail="RESEND_API_KEY nao configurada; configure em settings.",
        )
    resend = ResendClient(api_key=settings.resend_api_key)
    try:
        summary = await dispatch_contrato_alerts(
            db, resend, recipients=[str(r) for r in payload.recipients]
        )
    finally:
        await resend.aclose()
    return ContratoAlertaSummary(
        total_contratos=summary.total_contratos,
        sent=summary.sent,
        skipped=summary.skipped,
        failed=summary.failed,
        results=[
            {
                "contrato_id": r.contrato_id,
                "janela": r.janela,
                "status": r.status,
                "recipients": r.recipients,
                "resend_message_id": r.resend_message_id,
                "error_message": r.error_message,
            }
            for r in summary.results
        ],  # type: ignore[arg-type]
    )


@router.get("/contratos/{contrato_id}", response_model=ContratoRead)
async def get_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
) -> ContratoRead:
    row = await get_contrato(db, contrato_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.patch("/contratos/{contrato_id}", response_model=ContratoRead)
async def update_contrato_endpoint(
    contrato_id: int,
    payload: ContratoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    fields = payload.model_dump(exclude_unset=True)
    try:
        row = await update_contrato(
            db, contrato_id, actor=current_user.email, **fields
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.post("/contratos/{contrato_id}/arquivo", response_model=ContratoRead)
async def upload_arquivo_contrato_endpoint(
    contrato_id: int,
    arquivo: Annotated[UploadFile, File(description="PDF do contrato")],
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_contratos_storage),
    current_user: User = Depends(get_current_user),
) -> ContratoRead:
    """Anexa/substitui o PDF do contrato. Substituicao nao apaga o
    arquivo antigo do storage (historico barato; limpeza so no delete
    do contrato)."""
    content_type = (arquivo.content_type or "").lower()
    if content_type != "application/pdf":
        raise HTTPException(
            status_code=422,
            detail=(
                "arquivo deve ser PDF (content-type application/pdf); "
                f"recebido '{arquivo.content_type or 'desconhecido'}'"
            ),
        )
    content = await arquivo.read()
    if not content:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    if len(content) > MAX_CONTRATO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"arquivo excede o limite de {MAX_CONTRATO_UPLOAD_BYTES} "
                "bytes"
            ),
        )
    row = await set_arquivo_contrato(
        db,
        contrato_id,
        storage=storage,
        filename=arquivo.filename or "contrato.pdf",
        content=content,
        actor=current_user.email,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
    return _contrato_to_read(row)


@router.delete("/contratos/{contrato_id}", status_code=204)
async def delete_contrato_endpoint(
    contrato_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_contratos_storage),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await delete_contrato(
        db, contrato_id, storage=storage, actor=current_user.email
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Contrato nao encontrado")
