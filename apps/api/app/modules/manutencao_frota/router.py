"""Router HTTP do Modulo B (Frota) -- B.1 cadastro + B.3 consulta Detran.

Endpoints:
  - GET    /veiculos                lista com filtros
  - POST   /veiculos                cria
  - GET    /veiculos/{id}           detalhe
  - PATCH  /veiculos/{id}           update parcial
  - DELETE /veiculos/{id}           remove
  - POST   /veiculos/{id}/documentos        adiciona documento
  - PATCH  /veiculos/documentos/{doc_id}    atualiza documento
  - DELETE /veiculos/documentos/{doc_id}    remove documento
  - POST   /veiculos/{id}/consultar-detran  dispara consulta Infosimples (B.3)
  - GET    /veiculos/{id}/consultas         historico de consultas Detran
  - GET    /consultas-detran                lista global (filtros uf/page)
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.integrations.onedrive.client import build_onedrive_client
from app.integrations.onedrive.storage import OneDriveStorage
from app.modules.licitacoes.storage import EditaisStorage, LocalStorage
from app.modules.manutencao_frota import service
from app.modules.manutencao_frota.schemas import (
    ConsultaDetranListResponse,
    ConsultaDetranRead,
    ConsultaDetranRequest,
    DocumentoVeiculoCreate,
    DocumentoVeiculoRead,
    DocumentoVeiculoUpdate,
    ModuleStatus,
    ParteDiariaListResponse,
    ParteDiariaRead,
    ParteDiariaUpdate,
    VeiculoCreate,
    VeiculoListResponse,
    VeiculoRead,
    VeiculoUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status", response_model=ModuleStatus)
async def status_endpoint() -> ModuleStatus:
    return ModuleStatus(module="manutencao_frota", implemented=True)


# --- Veiculos ---------------------------------------------------------------


@router.get("/veiculos", response_model=VeiculoListResponse)
async def list_veiculos_endpoint(
    status: str | None = Query(None, max_length=16),
    obra: str | None = Query(None, max_length=128),
    tipo: str | None = Query(None, max_length=32),
    search: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> VeiculoListResponse:
    offset = (page - 1) * page_size
    rows, total = await service.list_veiculos(
        db,
        status=status,
        obra=obra,
        tipo=tipo,
        search=search,
        limit=page_size,
        offset=offset,
    )
    return VeiculoListResponse(
        items=[VeiculoRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/veiculos", response_model=VeiculoRead, status_code=201)
async def create_veiculo_endpoint(
    payload: VeiculoCreate,
    db: AsyncSession = Depends(get_db),
) -> VeiculoRead:
    data = payload.model_dump()
    documentos = data.pop("documentos", None) or None
    veiculo = await service.create_veiculo(
        db,
        documentos=documentos,
        **data,
    )
    return VeiculoRead.model_validate(veiculo)


@router.get("/veiculos/{veiculo_id}", response_model=VeiculoRead)
async def get_veiculo_endpoint(
    veiculo_id: int,
    db: AsyncSession = Depends(get_db),
) -> VeiculoRead:
    row = await service.get_veiculo(db, veiculo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return VeiculoRead.model_validate(row)


@router.patch("/veiculos/{veiculo_id}", response_model=VeiculoRead)
async def update_veiculo_endpoint(
    veiculo_id: int,
    payload: VeiculoUpdate,
    db: AsyncSession = Depends(get_db),
) -> VeiculoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_veiculo(db, veiculo_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return VeiculoRead.model_validate(row)


@router.delete("/veiculos/{veiculo_id}", status_code=204)
async def delete_veiculo_endpoint(
    veiculo_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await service.delete_veiculo(db, veiculo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")


# --- Documentos do veiculo --------------------------------------------------


@router.post(
    "/veiculos/{veiculo_id}/documentos",
    response_model=DocumentoVeiculoRead,
    status_code=201,
)
async def add_documento_endpoint(
    veiculo_id: int,
    payload: DocumentoVeiculoCreate,
    db: AsyncSession = Depends(get_db),
) -> DocumentoVeiculoRead:
    row = await service.add_documento(
        db, veiculo_id, **payload.model_dump()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return DocumentoVeiculoRead.model_validate(row)


@router.patch(
    "/veiculos/documentos/{documento_id}",
    response_model=DocumentoVeiculoRead,
)
async def update_documento_endpoint(
    documento_id: int,
    payload: DocumentoVeiculoUpdate,
    db: AsyncSession = Depends(get_db),
) -> DocumentoVeiculoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_documento(db, documento_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    return DocumentoVeiculoRead.model_validate(row)


@router.delete(
    "/veiculos/documentos/{documento_id}",
    status_code=204,
)
async def delete_documento_endpoint(
    documento_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await service.delete_documento(db, documento_id)
    if not ok:
        raise HTTPException(status_code=404, detail="documento nao encontrado")


# --- B.3 -- Consulta Detran (Infosimples) -----------------------------------


def get_infosimples_dep() -> Any:
    """Dependency override-friendly accessor para o singleton.

    Em testes, basta `app.dependency_overrides[get_infosimples_dep] =
    lambda: FakeClient()` -- sem precisar mexer no `_infosimples_singleton`
    global. Em prod retorna a instancia compartilhada.
    """
    return service.get_infosimples_singleton()


@router.post(
    "/veiculos/{veiculo_id}/consultar-detran",
    response_model=ConsultaDetranRead,
    status_code=201,
)
async def consultar_detran_endpoint(
    veiculo_id: int,
    payload: ConsultaDetranRequest,
    db: AsyncSession = Depends(get_db),
    client: Any = Depends(get_infosimples_dep),
) -> ConsultaDetranRead:
    try:
        consulta = await service.consultar_detran(
            db, veiculo_id, payload.uf, client=client
        )
    except ValueError as exc:
        # `veiculo nao encontrado` ou `uf nao suportada`. UF invalida
        # ja seria pega pelo schema; mantemos defesa-em-profundidade.
        msg = str(exc)
        status = 404 if "veiculo" in msg else 422
        raise HTTPException(status_code=status, detail=msg) from exc
    return ConsultaDetranRead.model_validate(consulta)


@router.get(
    "/veiculos/{veiculo_id}/consultas",
    response_model=ConsultaDetranListResponse,
)
async def list_consultas_veiculo_endpoint(
    veiculo_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ConsultaDetranListResponse:
    veiculo = await service.get_veiculo(db, veiculo_id)
    if veiculo is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    offset = (page - 1) * page_size
    rows, total = await service.list_consultas_detran(
        db, veiculo_id=veiculo_id, limit=page_size, offset=offset
    )
    return ConsultaDetranListResponse(
        items=[ConsultaDetranRead.model_validate(r) for r in rows],
        total=total,
    )


@router.get(
    "/consultas-detran",
    response_model=ConsultaDetranListResponse,
)
async def list_consultas_global_endpoint(
    uf: str | None = Query(None, max_length=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ConsultaDetranListResponse:
    offset = (page - 1) * page_size
    rows, total = await service.list_consultas_detran(
        db, uf=uf, limit=page_size, offset=offset
    )
    return ConsultaDetranListResponse(
        items=[ConsultaDetranRead.model_validate(r) for r in rows],
        total=total,
    )


# --- B.2 -- Parte Diaria (OCR via Document AI) ----------------------------


async def get_partes_diarias_storage() -> AsyncIterator[EditaisStorage]:
    """Storage dedicado para anexos de parte diaria.

    Mesmo backend (`STORAGE_BACKEND=local|onedrive`) do storage de
    editais/fiscal, em pasta separada (`parte_diaria_storage_subdir`)
    para nao misturar namespaces. Local: filesystem; OneDrive: pasta
    no drive Microsoft 365 do tenant.
    """
    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "onedrive":
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=settings.parte_diaria_storage_subdir,
        )
        try:
            yield OneDriveStorage(client)
        finally:
            await client.aclose()
        return
    base = Path(settings.editais_storage_path).parent
    yield LocalStorage(base / settings.parte_diaria_storage_subdir)


def get_documentai_dep() -> Any:
    """Singleton-friendly accessor para o `GoogleDocumentAIClient`."""
    return service.get_documentai_singleton()


@router.post(
    "/partes-diarias",
    response_model=ParteDiariaRead,
    status_code=201,
)
async def upload_parte_diaria_endpoint(
    arquivo: Annotated[UploadFile, File(description="PDF/JPG/PNG da parte diaria")],
    veiculo_id: Annotated[int | None, Form()] = None,
    obra: Annotated[str | None, Form()] = None,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_partes_diarias_storage),
    client: Any = Depends(get_documentai_dep),
) -> ParteDiariaRead:
    """Upload do anexo + OCR sincrono.

    Em prod o OCR roda no worker async para nao segurar a request,
    mas a versao sincrona aqui e util para a UI mostrar resultado
    imediato em arquivos pequenos. O endpoint dispara OCR direto;
    caso o caller queira async, basta `POST /partes-diarias/{id}/reprocessar`
    apos um upload sem OCR.
    """
    content = await arquivo.read()
    if not content:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    parte = await service.create_parte_diaria(
        db,
        content=content,
        filename=arquivo.filename or "parte-diaria.pdf",
        mime_type=arquivo.content_type or "application/octet-stream",
        storage=storage,
    )
    # Pre-fill por form fields (operador conhece o veiculo/obra antes
    # mesmo do OCR rodar). Assim, se OCR falhar, dados manuais nao
    # somem.
    if veiculo_id or obra:
        await service.update_parte_diaria(
            db,
            parte.id,
            {k: v for k, v in {"veiculo_id": veiculo_id, "obra": obra}.items() if v},
        )
    parte = await service.processar_ocr_parte_diaria(
        db, parte.id, client=client, storage=storage
    )
    return ParteDiariaRead.model_validate(parte)


@router.get(
    "/partes-diarias",
    response_model=ParteDiariaListResponse,
)
async def list_partes_diarias_endpoint(
    veiculo_id: int | None = Query(None),
    obra: str | None = Query(None, max_length=200),
    ocr_status: str | None = Query(None, max_length=16),
    placa: str | None = Query(None, max_length=8),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ParteDiariaListResponse:
    offset = (page - 1) * page_size
    rows, total = await service.list_partes_diarias(
        db,
        veiculo_id=veiculo_id,
        obra=obra,
        ocr_status=ocr_status,
        placa=placa,
        limit=page_size,
        offset=offset,
    )
    return ParteDiariaListResponse(
        items=[ParteDiariaRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/partes-diarias/{parte_id}",
    response_model=ParteDiariaRead,
)
async def get_parte_diaria_endpoint(
    parte_id: int,
    db: AsyncSession = Depends(get_db),
) -> ParteDiariaRead:
    row = await service.get_parte_diaria(db, parte_id)
    if row is None:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")
    return ParteDiariaRead.model_validate(row)


@router.patch(
    "/partes-diarias/{parte_id}",
    response_model=ParteDiariaRead,
)
async def update_parte_diaria_endpoint(
    parte_id: int,
    payload: ParteDiariaUpdate,
    db: AsyncSession = Depends(get_db),
) -> ParteDiariaRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_parte_diaria(db, parte_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")
    return ParteDiariaRead.model_validate(row)


@router.delete(
    "/partes-diarias/{parte_id}",
    status_code=204,
)
async def delete_parte_diaria_endpoint(
    parte_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_partes_diarias_storage),
) -> None:
    ok = await service.delete_parte_diaria(db, parte_id, storage=storage)
    if not ok:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")


@router.post(
    "/partes-diarias/{parte_id}/reprocessar",
    response_model=ParteDiariaRead,
)
async def reprocessar_parte_diaria_endpoint(
    parte_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_partes_diarias_storage),
    client: Any = Depends(get_documentai_dep),
) -> ParteDiariaRead:
    """Re-roda OCR (util quando o operador subiu arquivo melhor ou
    quando troca-se o processor do Document AI no console)."""
    parte = await service.get_parte_diaria(db, parte_id)
    if parte is None:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")
    parte = await service.processar_ocr_parte_diaria(
        db, parte_id, client=client, storage=storage
    )
    return ParteDiariaRead.model_validate(parte)
