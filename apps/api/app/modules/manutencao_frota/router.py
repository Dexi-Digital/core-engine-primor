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
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.licitacoes.storage import EditaisStorage
from app.modules.manutencao_frota import service
from app.modules.manutencao_frota.schemas import (
    ConsultaDetranListResponse,
    ConsultaDetranRead,
    ConsultaDetranRequest,
    DocumentoVeiculoCreate,
    DocumentoVeiculoRead,
    DocumentoVeiculoUpdate,
    ModuleStatus,
    ParteDiariaConsumo,
    ParteDiariaListResponse,
    ParteDiariaManualCreate,
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
    current_user: User = Depends(get_current_user),
) -> VeiculoRead:
    data = payload.model_dump()
    documentos = data.pop("documentos", None) or None
    veiculo = await service.create_veiculo(
        db,
        documentos=documentos,
        actor=current_user.email,
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
    current_user: User = Depends(get_current_user),
) -> VeiculoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_veiculo(
        db, veiculo_id, actor=current_user.email, **fields
    )
    if row is None:
        raise HTTPException(status_code=404, detail="veiculo nao encontrado")
    return VeiculoRead.model_validate(row)


@router.delete("/veiculos/{veiculo_id}", status_code=204)
async def delete_veiculo_endpoint(
    veiculo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await service.delete_veiculo(
        db, veiculo_id, actor=current_user.email
    )
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
    current_user: User = Depends(get_current_user),
) -> DocumentoVeiculoRead:
    row = await service.add_documento(
        db, veiculo_id, actor=current_user.email, **payload.model_dump()
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
    current_user: User = Depends(get_current_user),
) -> DocumentoVeiculoRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_documento(
        db, documento_id, actor=current_user.email, **fields
    )
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
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await service.delete_documento(
        db, documento_id, actor=current_user.email
    )
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
    current_user: User = Depends(get_current_user),
) -> ConsultaDetranRead:
    try:
        consulta = await service.consultar_detran(
            db, veiculo_id, payload.uf, client=client, actor=current_user.email
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

    Delega para `service.open_partes_diarias_storage` (helper
    compartilhado API/worker) para garantir que ambos usem o mesmo
    backend conforme `STORAGE_BACKEND`. Sem essa fatoracao, API podia
    salvar em OneDrive enquanto worker tentava ler com LocalStorage --
    bug apontado pelo Devin Review.
    """
    settings = get_settings()
    async with service.open_partes_diarias_storage(settings) as storage:
        yield storage


def get_documentai_dep() -> Any:
    """Singleton-friendly accessor para o `GoogleDocumentAIClient`.

    Mantido para retrocompatibilidade mas nao e mais usado pelo router
    (OCR roda no worker via `get_ocr_dispatcher`). Util caso alguem
    monte um endpoint custom que chame OCR sincrono fora do flow B.2.
    """
    return service.get_documentai_singleton()


# AGENTS.md: OCR roda SEMPRE no worker (Celery). O dispatcher e
# injetado para que testes possam substituir por uma versao "eager"
# (executa OCR sincrono in-process, no mesmo db session da request),
# sem precisar de Redis/celery rodando.
#
# `actor` e o email do usuario HTTP que disparou o dispatch -- propagado
# pros args do send_task para que o `audit_log` do OCR async identifique
# o uploader humano (senao todo OCR ficava como actor=`system`).
OcrDispatcher = Callable[[int, str], Awaitable[None]]


def get_ocr_dispatcher() -> OcrDispatcher:
    async def _dispatch(parte_id: int, actor: str) -> None:
        # O `send_task` da Celery e sync e tipicamente retorna em <5ms
        # (so escreve no broker Redis) -- nao bloqueia request.
        service.enqueue_ocr_parte_diaria(parte_id, actor=actor)

    return _dispatch


@router.post(
    "/partes-diarias",
    response_model=ParteDiariaRead,
    status_code=202,
)
async def upload_parte_diaria_endpoint(
    arquivo: Annotated[UploadFile, File(description="PDF/JPG/PNG da parte diaria")],
    veiculo_id: Annotated[int | None, Form()] = None,
    obra: Annotated[str | None, Form()] = None,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_partes_diarias_storage),
    dispatch_ocr: OcrDispatcher = Depends(get_ocr_dispatcher),
    current_user: User = Depends(get_current_user),
) -> ParteDiariaRead:
    """Upload do anexo + dispatch da task OCR para o worker (fila `manutencao`).

    Conforme AGENTS.md, OCR roda SEMPRE no worker -- nunca bloqueia a API.
    O endpoint:
      1. Cria a row com `ocr_status='pendente'` e persiste o anexo.
      2. Aplica pre-fill manual (`veiculo_id`/`obra` opcionais).
      3. Dispatcha `worker.tasks.manutencao.ocr_parte_diaria` por nome.
      4. Devolve 202 + ParteDiariaRead com status `pendente`.

    A UI faz polling/refresh para ver o status virar `processado`/`erro`.
    Falha do dispatcher (broker down) marca a row como `erro` para o
    operador poder retentar via `/reprocessar` quando o broker voltar.
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
        actor=current_user.email,
    )
    # Pre-fill por form fields (operador conhece o veiculo/obra antes
    # mesmo do OCR rodar). Assim, se OCR falhar, dados manuais nao
    # somem.
    if veiculo_id or obra:
        await service.update_parte_diaria(
            db,
            parte.id,
            {k: v for k, v in {"veiculo_id": veiculo_id, "obra": obra}.items() if v},
            actor=current_user.email,
        )
    try:
        await dispatch_ocr(parte.id, current_user.email)
    except Exception as exc:
        # Broker indisponivel: marca como erro com mensagem clara para
        # o operador. Anexo ja esta persistido entao reprocessar funciona.
        logger.warning("Falha ao enfileirar OCR parte_diaria=%s: %s", parte.id, exc)
        await service.mark_parte_diaria_erro(
            db, parte.id, f"falha ao enfileirar OCR: {exc}", actor=current_user.email
        )
    parte = await service.get_parte_diaria(db, parte.id)
    return ParteDiariaRead.model_validate(parte)


@router.post(
    "/partes-diarias/manual",
    response_model=ParteDiariaRead,
)
async def create_parte_diaria_manual_endpoint(
    payload: ParteDiariaManualCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ParteDiariaRead:
    """Apontamento manual via PWA mobile (D5 fase 2).

    Sem upload de PDF/foto, sem OCR -- o apontador em campo informa
    os campos estruturados direto pelo formulario do PWA. Row criada
    com `ocr_status='revisado'` (entrada manual ja conferida) e
    `ocr_source='manual_pwa'`.

    Idempotencia via `client_uuid`: se o PWA reenviar a mesma parte
    (retry pos-reconexao depois de cair conexao), o backend devolve a
    row existente em vez de duplicar. Status code:
      - 201 Created  -> nova row
      - 200 OK       -> duplicata (ja existia, devolve a primeira)
    """
    try:
        parte, criada_agora = await service.create_parte_diaria_manual(
            db,
            payload=payload.model_dump(),
            actor=current_user.email,
        )
    except ValueError as exc:
        # Validacao de dominio (horimetro/km invalido, FK de
        # veiculo_id inexistente). Service segue convencao do
        # resto do modulo (consultar_detran etc) levantando
        # ValueError; router converte em 422.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.status_code = 201 if criada_agora else 200
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


@router.get(
    "/partes-diarias/{parte_id}/consumo",
    response_model=ParteDiariaConsumo,
)
async def get_consumo_parte_diaria_endpoint(
    parte_id: int,
    db: AsyncSession = Depends(get_db),
) -> ParteDiariaConsumo:
    """Calculo derivado de consumo (D5).

    horas_trabalhadas = horimetro_fim - horimetro_inicio
    consumo_l/h = combustivel_litros / horas_trabalhadas
    consumo_km/l = km_rodados / combustivel_litros
    alerta_manutencao_preventiva = atravessou multiplo de 250h?

    Calculado on-the-fly -- nao gravado no banco para nao
    inconsistir se operador editar campos depois.
    """
    consumo = await service.get_consumo_parte_diaria(db, parte_id)
    if consumo is None:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")
    return ParteDiariaConsumo(**consumo)


@router.patch(
    "/partes-diarias/{parte_id}",
    response_model=ParteDiariaRead,
)
async def update_parte_diaria_endpoint(
    parte_id: int,
    payload: ParteDiariaUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ParteDiariaRead:
    fields = payload.model_dump(exclude_unset=True)
    row = await service.update_parte_diaria(
        db, parte_id, fields, actor=current_user.email
    )
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
    current_user: User = Depends(get_current_user),
) -> None:
    ok = await service.delete_parte_diaria(
        db, parte_id, storage=storage, actor=current_user.email
    )
    if not ok:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")


@router.post(
    "/partes-diarias/{parte_id}/reprocessar",
    response_model=ParteDiariaRead,
    status_code=202,
)
async def reprocessar_parte_diaria_endpoint(
    parte_id: int,
    db: AsyncSession = Depends(get_db),
    dispatch_ocr: OcrDispatcher = Depends(get_ocr_dispatcher),
    current_user: User = Depends(get_current_user),
) -> ParteDiariaRead:
    """Re-dispatcha a task OCR (util pos-erro ou troca de processor).

    Igual ao upload: roda no worker via Celery, nao bloqueia a API.
    Devolve 202 com a row em `ocr_status='pendente'` -- a UI polla
    ate virar `processado` ou `erro`.
    """
    parte = await service.get_parte_diaria(db, parte_id)
    if parte is None:
        raise HTTPException(status_code=404, detail="parte_diaria nao encontrada")
    # Reseta status para pendente (caso esteja em 'erro' ou 'processado').
    await service.update_parte_diaria(
        db,
        parte_id,
        {"ocr_status": "pendente", "ocr_error_msg": None},
        actor=current_user.email,
    )
    try:
        await dispatch_ocr(parte_id, current_user.email)
    except Exception as exc:
        logger.warning("Falha ao re-enfileirar OCR parte_diaria=%s: %s", parte_id, exc)
        await service.mark_parte_diaria_erro(
            db, parte_id, f"falha ao enfileirar OCR: {exc}", actor=current_user.email
        )
    parte = await service.get_parte_diaria(db, parte_id)
    return ParteDiariaRead.model_validate(parte)
