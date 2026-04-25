"""HTTP router para o modulo fiscal (Modulo C).

Endpoints:
  - POST   /documentos                  upload XML (multipart)
  - GET    /documentos                  lista com filtros
  - GET    /documentos/{id}             detalhe
  - PATCH  /documentos/{id}             update parcial (observacoes/status)
  - DELETE /documentos/{id}             remove
  - POST   /documentos/{id}/enviar-dominio   dispara envio sincrono
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.dp_sesmt.schemas import ModuleStatus
from app.modules.fiscal.parser import FiscalParseError
from app.modules.fiscal.schemas import (
    DocumentoFiscalEnvioResponse,
    DocumentoFiscalRead,
    DocumentoFiscalUpdate,
)
from app.modules.fiscal.service import (
    FiscalDuplicateError,
    delete_documento,
    enviar_para_dominio,
    get_documento,
    get_dominio_singleton,
    import_xml,
    list_documentos,
    update_documento,
)
from app.modules.licitacoes.router import get_editais_storage
from app.modules.licitacoes.storage import EditaisStorage

router = APIRouter()


def get_dominio_dep() -> Any:
    """Devolve o `DominioClient` singleton compartilhado por processo.

    Reuso do client e CRITICO em prod: ele cacheia o token OAuth2 (TTL
    ~1h) e mantem um pool TCP do httpx vivo. Criar uma instancia por
    request quebraria os dois -- a Domínio rate-limita `/token` e cada
    novo pool gasta uma RTT de TLS handshake.

    O `aclose()` nao acontece aqui (seria absurdo fechar a cada request);
    quem fecha e o lifespan handler do FastAPI no shutdown da API. Em
    testes, `app.dependency_overrides[get_dominio_dep]` substitui o
    singleton normalmente.
    """
    return get_dominio_singleton()


@router.get("/status", response_model=ModuleStatus)
async def status_endpoint() -> ModuleStatus:
    return ModuleStatus(module="fiscal", implemented=True)


@router.post("/documentos", response_model=DocumentoFiscalRead, status_code=201)
async def upload_documento(
    arquivo: Annotated[UploadFile, File(description="XML fiscal a importar")],
    source: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_editais_storage),
) -> DocumentoFiscalRead:
    """Recebe um XML, parseia, persiste storage + DB.

    Status `409 Conflict` se o XML ja foi importado (idempotencia).
    Status `422 Unprocessable Entity` se o XML estiver malformado.
    """
    content = await arquivo.read()
    if not content:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    try:
        doc = await import_xml(
            db,
            xml_bytes=content,
            storage=storage,
            filename=arquivo.filename or "documento.xml",
            source=source,
        )
    except FiscalParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FiscalDuplicateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_id": exc.existing_id,
            },
        ) from exc
    return DocumentoFiscalRead.model_validate(doc)


@router.get("/documentos", response_model=list[DocumentoFiscalRead])
async def list_endpoint(
    tipo: str | None = Query(default=None, max_length=16),
    status_envio: str | None = Query(default=None, max_length=16),
    emitente_cnpj: str | None = Query(default=None, max_length=20),
    destinatario_cnpj: str | None = Query(default=None, max_length=20),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentoFiscalRead]:
    items = await list_documentos(
        db,
        tipo=tipo,
        status_envio=status_envio,
        emitente_cnpj=emitente_cnpj,
        destinatario_cnpj=destinatario_cnpj,
        search=search,
        limit=limit,
        offset=offset,
    )
    return [DocumentoFiscalRead.model_validate(i) for i in items]


@router.get("/documentos/{doc_id}", response_model=DocumentoFiscalRead)
async def get_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
) -> DocumentoFiscalRead:
    doc = await get_documento(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    return DocumentoFiscalRead.model_validate(doc)


@router.patch("/documentos/{doc_id}", response_model=DocumentoFiscalRead)
async def update_endpoint(
    doc_id: int,
    payload: DocumentoFiscalUpdate,
    db: AsyncSession = Depends(get_db),
) -> DocumentoFiscalRead:
    try:
        doc = await update_documento(
            db,
            doc_id,
            observacoes=payload.observacoes,
            status_envio=payload.status_envio,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    return DocumentoFiscalRead.model_validate(doc)


@router.delete("/documentos/{doc_id}", status_code=204)
async def delete_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_editais_storage),
) -> None:
    ok = await delete_documento(db, doc_id, storage=storage)
    if not ok:
        raise HTTPException(status_code=404, detail="documento nao encontrado")


@router.post(
    "/documentos/{doc_id}/enviar-dominio",
    response_model=DocumentoFiscalEnvioResponse,
)
async def enviar_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_editais_storage),
    dominio_client: Any = Depends(get_dominio_dep),
) -> DocumentoFiscalEnvioResponse:
    """Dispara envio sincrono para a Dominio.

    Retorna o status final (enviado/erro) + protocolo. Em prod, a UI
    pode chamar este endpoint diretamente OU enfileirar via worker
    para fazer retry com backoff em caso de erro de transporte.
    """
    doc = await get_documento(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    if doc.status_envio == "enviado":
        # Idempotencia: ja foi enviado, devolve o protocolo cached.
        return DocumentoFiscalEnvioResponse(
            documento_id=doc.id,
            status_envio=doc.status_envio,
            enqueued=False,
            protocolo_dominio=doc.protocolo_dominio,
        )
    doc = await enviar_para_dominio(
        db, doc_id, dominio_client=dominio_client, storage=storage
    )
    return DocumentoFiscalEnvioResponse(
        documento_id=doc.id,
        status_envio=doc.status_envio,
        enqueued=False,
        protocolo_dominio=doc.protocolo_dominio,
        error_msg=doc.error_msg,
    )
