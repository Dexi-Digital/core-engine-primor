"""API financeira: controles autenticados e transferência direta de arquivos grandes."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import ALGORITHM, decode_token
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User
from app.modules.auth.permissions import has_at_least
from app.modules.financeiro_importacao import service as svc
from app.modules.financeiro_importacao.models import ImportacaoFinanceira
from app.modules.financeiro_importacao.parser import MAX_BYTES
from app.modules.financeiro_importacao.schemas import (
    ConfirmarImportacao,
    Filtros,
    LoteRead,
    PrepararImportacao,
)
from app.modules.financeiro_totvs.models import TotvsLancamento, TotvsSyncLog
from app.modules.licitacoes.storage import EditaisStorage
from app.modules.licitacoes.storage_factory import editais_storage

router = APIRouter(prefix="/api/v1/financeiro/importacoes", tags=["financeiro-importacao"])
operador = require_role("operador", modulo="financeiro")


async def get_storage() -> AsyncIterator[EditaisStorage]:
    async with editais_storage(get_settings()) as storage:
        yield storage


def _token(user: User, lote_id: int, purpose: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "type": "financeiro_arquivo",
            "lote_id": lote_id,
            "purpose": purpose,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )


async def _usuario_arquivo(request: Request, db: AsyncSession, lote_id: int, purpose: str) -> User:
    """A capacidade temporária só autoriza um arquivo, nunca os demais endpoints."""
    try:
        scheme, token = request.headers.get("authorization", "").split(" ", 1)
        payload = decode_token(token)
        if (
            scheme.lower() != "bearer"
            or payload.get("type") != "financeiro_arquivo"
            or payload.get("lote_id") != lote_id
            or payload.get("purpose") != purpose
        ):
            raise ValueError
        user = await db.get(User, int(payload["sub"]))
        if user is None or not user.is_active:
            raise ValueError
    except (JWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(401, "Autorização de arquivo inválida ou expirada") from exc
    if purpose == "upload" and not has_at_least(user, modulo="financeiro", required="operador"):
        raise HTTPException(403, "Sem permissão para importar")
    return user


async def _lote(db: AsyncSession, lote_id: int, *, lock: bool = False) -> ImportacaoFinanceira:
    q = select(ImportacaoFinanceira).where(ImportacaoFinanceira.id == lote_id)
    if lock:
        q = q.with_for_update()
    lote = (await db.execute(q)).scalar_one_or_none()
    if lote is None:
        raise HTTPException(404, "Importação não encontrada")
    return lote


@router.post("", status_code=201)
async def preparar(
    payload: PrepararImportacao, db: AsyncSession = Depends(get_db), user: User = Depends(operador)
) -> dict:
    lote = await svc.preparar(
        db, nome=payload.nome_arquivo, sha256=payload.sha256, actor=user.email
    )
    return {
        "lote": LoteRead.model_validate(lote),
        "upload_token": _token(user, lote.id, "upload")
        if lote.status == "aguardando_arquivo"
        else None,
    }


@router.get("")
async def lotes(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    rows = (
        (
            await db.execute(
                select(ImportacaoFinanceira).order_by(ImportacaoFinanceira.id.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "ativo_id": (await svc.base_ativa(db)).lote_id,
        "data": [LoteRead.model_validate(row) for row in rows],
    }


@router.get("/totvs")
async def consultar_totvs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    situacao: str | None = Query(None, max_length=32),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Expõe os dados e o histórico RM existentes, sem completar campos ausentes."""
    query = select(TotvsLancamento)
    if situacao:
        query = query.where(TotvsLancamento.situacao == situacao)
    subquery = query.subquery()
    total, valor = (
        await db.execute(
            select(
                select(func.count()).select_from(subquery).scalar_subquery(),
                select(func.sum(subquery.c.valor)).scalar_subquery(),
            )
        )
    ).one()
    rows = (
        (
            await db.execute(
                query.order_by(
                    TotvsLancamento.data_emissao.desc().nullslast(), TotvsLancamento.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    logs = (
        (
            await db.execute(
                select(TotvsSyncLog)
                .order_by(TotvsSyncLog.started_at.desc(), TotvsSyncLog.id.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "valor_original": str(valor or 0),
        "data": [
            {
                field: getattr(row, field)
                for field in (
                    "external_id",
                    "codcoligada",
                    "codfilial",
                    "idlan",
                    "contraparte_documento",
                    "contraparte_nome",
                    "data_emissao",
                    "data_vencimento",
                    "valor",
                    "valor_baixado",
                    "saldo",
                    "status_rm",
                    "situacao",
                    "extractor",
                )
            }
            for row in rows
        ],
        "sincronizacoes": [
            {
                field: getattr(log, field)
                for field in (
                    "source",
                    "janela",
                    "extractor",
                    "status",
                    "lidos",
                    "gravados",
                    "error_message",
                    "started_at",
                    "finished_at",
                )
            }
            for log in logs
        ],
    }


@router.get("/apropriacoes")
async def apropriacoes(
    filtros: Filtros = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    try:
        return await svc.listar(db, (await svc.base_ativa(db)).lote_id, filtros)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{lote_id}", response_model=LoteRead)
async def detalhe(
    lote_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    return await _lote(db, lote_id)


@router.get("/{lote_id}/previa")
async def previa(
    lote_id: int,
    filtros: Filtros = Depends(),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    await _lote(db, lote_id)
    try:
        return await svc.listar(db, lote_id, filtros)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{lote_id}/arquivo", status_code=202)
async def enviar(
    lote_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_storage),
) -> dict:
    user = await _usuario_arquivo(request, db, lote_id, "upload")
    lote = await _lote(db, lote_id, lock=True)
    if lote.status != "aguardando_arquivo":
        raise HTTPException(409, "Arquivo já recebido; atualize o lote")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_BYTES:
            raise HTTPException(413, "Arquivo excede 30 MB")
    if not content or hashlib.sha256(content).hexdigest() != lote.sha256:
        raise HTTPException(422, "Arquivo vazio ou diferente do arquivo selecionado")
    lote.storage_path = await svc.salvar_arquivo(storage, lote.id, "original", bytes(content))
    try:
        await svc.enfileirar(db, lote, user.email)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"id": lote.id, "status": "na_fila"}


@router.post("/{lote_id}/reprocessar", status_code=202)
async def reprocessar(
    lote_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(operador)
) -> dict:
    lote = await _lote(db, lote_id, lock=True)
    updated = (
        lote.atualizado_em.replace(tzinfo=UTC)
        if lote.atualizado_em.tzinfo is None
        else lote.atualizado_em
    )
    travado = lote.status == "na_fila" and datetime.now(UTC) - updated > timedelta(minutes=30)
    if not lote.storage_path or (
        lote.status not in ("erro_fila", "erro_processamento") and not travado
    ):
        raise HTTPException(409, "Este lote não precisa de reprocessamento")
    try:
        await svc.enfileirar(db, lote, user.email)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"status": "na_fila"}


@router.post("/{lote_id}/confirmar")
async def confirmar(
    lote_id: int,
    payload: ConfirmarImportacao,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(operador),
) -> dict:
    await _lote(db, lote_id)
    try:
        await svc.confirmar(db, lote_id, payload.substituir_lote_id, user.email)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "ativo"}


@router.post("/{lote_id}/download-token")
async def download_token(
    lote_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    lote = await _lote(db, lote_id)
    if not lote.export_path or lote.status not in ("validado", "ativo", "historico"):
        raise HTTPException(409, "Exportação disponível após validação sem erros")
    return {"token": _token(user, lote_id, "download")}


@router.get("/{lote_id}/arquivo")
async def baixar(
    lote_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_storage),
) -> Response:
    user = await _usuario_arquivo(request, db, lote_id, "download")
    lote = await _lote(db, lote_id)
    if not lote.export_path or lote.status not in ("validado", "ativo", "historico"):
        raise HTTPException(409, "Exportação indisponível")
    content = await storage.read(lote.export_path)
    svc.auditar(db, lote_id, user.email, "export")
    await db.commit()
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="financeiro-{lote_id}.xlsx"',
            "Cache-Control": "no-store",
        },
    )
