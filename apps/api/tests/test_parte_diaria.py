"""Testes de parte diaria (Modulo B.2) -- service + router + storage."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.main import app
from app.modules.manutencao_frota import service
from app.modules.manutencao_frota.models import (
    PARTE_ERRO,
    PARTE_PROCESSADO,
    PARTE_REVISADO,
    ParteDiaria,
)
from app.modules.manutencao_frota.router import (
    get_ocr_dispatcher,
    get_partes_diarias_storage,
)

# --- fakes ------------------------------------------------------------------


class FakeOkOcrClient:
    """Devolve um payload normalizado fixo (mesmo formato do real)."""

    is_mock = False
    name = "google_documentai"

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._payload = payload

    async def processar_documento(
        self, *, content: bytes, mime_type: str, filename: str | None = None
    ) -> dict[str, Any]:
        self.calls.append({"size": len(content), "mime": mime_type, "filename": filename})
        if self._payload is not None:
            return self._payload
        return {
            "raw_text": "PARTE DIARIA\nOperador: Joao\n",
            "fields": {
                "data": "2025-08-15",
                "operador": "Joao da Silva",
                "obra": "Obra Norte",
                "equipamento": "VW/CONSTELLATION 24.280",
                "placa": "ABC1234",
                "horimetro_inicio": 1234.5,
                "horimetro_fim": 1278.9,
                "km_inicio": 45000,
                "km_fim": 45230,
            },
            "confidence": 0.92,
            "raw_response": {"document": {"entities": []}},
            "source": "google_documentai",
        }

    async def aclose(self) -> None:  # pragma: no cover
        return None


class FakeFailingOcrClient:
    is_mock = False
    name = "google_documentai"

    async def processar_documento(self, **_kw: Any) -> dict[str, Any]:
        raise RuntimeError("documentai timeout")


class InMemoryStorage:
    """Storage em memoria que satisfaz o contrato `EditaisStorage`.

    Identifica arquivos por `(licitacao_id, filename)` -- o adapter
    real de partes diarias usa `parte.id` como `licitacao_id` (nome
    legado).
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def save(
        self,
        *,
        licitacao_id: int,
        filename: str,
        content: AsyncIterator[bytes],
    ) -> tuple[str, int]:
        buf = io.BytesIO()
        async for chunk in content:
            buf.write(chunk)
        size = buf.tell()
        path = f"mem://{licitacao_id}/{filename}"
        self._data[path] = buf.getvalue()
        return path, size

    async def read(self, storage_path: str) -> bytes:
        return self._data[storage_path]

    async def delete(self, storage_path: str) -> None:
        self._data.pop(storage_path, None)


@pytest.fixture
def in_memory_storage():
    storage = InMemoryStorage()

    async def _gen() -> AsyncIterator[InMemoryStorage]:
        yield storage

    app.dependency_overrides[get_partes_diarias_storage] = _gen
    yield storage
    app.dependency_overrides.pop(get_partes_diarias_storage, None)


def _eager_factory(client: Any, db_session: AsyncSession, storage: Any):
    """Substitui o dispatcher Celery por execucao sincrona in-process.

    Conforme AGENTS.md, OCR roda no worker em prod -- mas como testes de
    API nao trazem broker/worker rodando, usamos este "eager dispatcher"
    que chama `processar_ocr_parte_diaria` direto na mesma session da
    request. Comportamento end-to-end pos-task fica equivalente: a row
    sai com `ocr_status='processado'` (ou 'erro') antes do response
    voltar para o caller, e a UI nao precisa esperar polling no teste.
    """

    async def _dispatch(parte_id: int) -> None:
        await service.processar_ocr_parte_diaria(
            db_session, parte_id, client=client, storage=storage
        )

    def _factory():
        return _dispatch

    return _factory


@pytest.fixture
def fake_ok_ocr(db_session: AsyncSession, in_memory_storage: InMemoryStorage):
    client = FakeOkOcrClient()
    app.dependency_overrides[get_ocr_dispatcher] = _eager_factory(
        client, db_session, in_memory_storage
    )
    yield client
    app.dependency_overrides.pop(get_ocr_dispatcher, None)


@pytest.fixture
def fake_failing_ocr(db_session: AsyncSession, in_memory_storage: InMemoryStorage):
    client = FakeFailingOcrClient()
    app.dependency_overrides[get_ocr_dispatcher] = _eager_factory(
        client, db_session, in_memory_storage
    )
    yield client
    app.dependency_overrides.pop(get_ocr_dispatcher, None)


# --- endpoint POST /partes-diarias -----------------------------------------


@pytest.mark.asyncio
async def test_upload_extrai_campos_e_persiste(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    files = {"arquivo": ("parte-001.pdf", b"%PDF-fake-bytes", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["ocr_status"] == PARTE_PROCESSADO
    assert body["operador"] == "Joao da Silva"
    assert body["obra"] == "Obra Norte"
    assert body["data"] == "2025-08-15"
    assert body["placa"] == "ABC1234"
    assert float(body["horimetro_inicio"]) == 1234.5
    assert body["km_fim"] == 45230
    assert body["ocr_confidence"] is not None
    assert body["anexo_path"].startswith("mem://")
    # Anexo de fato persistido no storage.
    assert any("parte-001.pdf" in p for p in in_memory_storage._data)
    # Adapter foi chamado uma vez com bytes corretos.
    assert len(fake_ok_ocr.calls) == 1
    assert fake_ok_ocr.calls[0]["mime"] == "application/pdf"


@pytest.mark.asyncio
async def test_upload_arquivo_vazio_devolve_422(
    api_client: AsyncClient,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    files = {"arquivo": ("vazio.pdf", b"", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_amarra_veiculo_via_placa_extraida(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    """Quando OCR extrai placa que existe no cadastro, parte_diaria.veiculo_id
    e setado automaticamente."""
    # Cria veiculo com mesma placa que o mock OCR retorna.
    rv = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABC1234", "renavam": "12345678900"},
        headers=auth_headers,
    )
    veiculo_id = rv.json()["id"]

    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    assert r.status_code == 202
    assert r.json()["veiculo_id"] == veiculo_id


@pytest.mark.asyncio
async def test_ocr_falha_devolve_201_com_status_erro(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_failing_ocr: FakeFailingOcrClient,
    auth_headers: dict[str, str],
) -> None:
    """Document AI fora do ar -> parte registrada com status='erro'.

    Endpoint NAO devolve 5xx -- UI renderiza erro inline e operador
    pode reprocessar."""
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    assert r.status_code == 202
    body = r.json()
    assert body["ocr_status"] == PARTE_ERRO
    assert "documentai timeout" in body["ocr_error_msg"]
    # Anexo continua persistido (operador pode reprocessar depois).
    assert body["anexo_path"] is not None


@pytest.mark.asyncio
async def test_audit_log_registra_create_e_update(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    assert r.status_code == 202
    parte_id = r.json()["id"]
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "manutencao_frota.parte_diaria",
                    AuditLog.resource_id == parte_id,
                )
            )
        )
        .scalars()
        .all()
    )
    actions = [a.action for a in rows]
    assert "create" in actions  # upload
    assert "update" in actions  # OCR processado


# --- update / revisao manual -----------------------------------------------


@pytest.mark.asyncio
async def test_patch_promove_status_para_revisado(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    """Operador ajusta um campo -> status auto vira `revisado`."""
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    parte_id = r.json()["id"]
    assert r.json()["ocr_status"] == PARTE_PROCESSADO

    r2 = await api_client.patch(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}",
        json={"operador": "Pedro Corrigido"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["operador"] == "Pedro Corrigido"
    assert r2.json()["ocr_status"] == PARTE_REVISADO


@pytest.mark.asyncio
async def test_patch_status_explicito_e_validado(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    parte_id = r.json()["id"]

    r_bad = await api_client.patch(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}",
        json={"ocr_status": "fantasma"},
        headers=auth_headers,
    )
    assert r_bad.status_code == 422


@pytest.mark.asyncio
async def test_patch_404_em_id_inexistente(
    api_client: AsyncClient,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    r = await api_client.patch(
        "/api/v1/manutencao-frota/partes-diarias/999",
        json={"operador": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 404


# --- listagem + detalhe ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_filtra_por_status_e_obra(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    # 2 uploads -> ambos com status=processado, obra=Obra Norte (mock).
    for fn in ("p1.pdf", "p2.pdf"):
        await api_client.post(
            "/api/v1/manutencao-frota/partes-diarias",
            files={"arquivo": (fn, b"%PDF-x", "application/pdf")},
            headers=auth_headers,
        )

    r = await api_client.get(
        "/api/v1/manutencao-frota/partes-diarias",
        params={"ocr_status": PARTE_PROCESSADO},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(it["ocr_status"] == PARTE_PROCESSADO for it in body["items"])

    r2 = await api_client.get(
        "/api/v1/manutencao-frota/partes-diarias",
        params={"obra": "Obra Norte"},
    )
    assert r2.json()["total"] == 2

    r3 = await api_client.get(
        "/api/v1/manutencao-frota/partes-diarias",
        params={"obra": "Obra Inexistente"},
    )
    assert r3.json()["total"] == 0


# --- reprocessar -----------------------------------------------------------


@pytest.mark.asyncio
async def test_reprocessar_recupera_de_erro(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    auth_headers: dict[str, str],
) -> None:
    """Upload com OCR falhando -> swap do dispatcher pelo OK ->
    reprocessar deve atualizar para `processado`."""
    failing = FakeFailingOcrClient()
    app.dependency_overrides[get_ocr_dispatcher] = _eager_factory(
        failing, db_session, in_memory_storage
    )
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    parte_id = r.json()["id"]
    assert r.json()["ocr_status"] == PARTE_ERRO

    # Substitui pelo OK e reprocessa.
    ok = FakeOkOcrClient()
    app.dependency_overrides[get_ocr_dispatcher] = _eager_factory(ok, db_session, in_memory_storage)
    try:
        r2 = await api_client.post(
            f"/api/v1/manutencao-frota/partes-diarias/{parte_id}/reprocessar", headers=auth_headers
        )
        assert r2.status_code == 202
        body = r2.json()
        assert body["ocr_status"] == PARTE_PROCESSADO
        assert body["operador"] == "Joao da Silva"
    finally:
        app.dependency_overrides.pop(get_ocr_dispatcher, None)


@pytest.mark.asyncio
async def test_dispatcher_broker_down_marca_erro(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    auth_headers: dict[str, str],
) -> None:
    """Broker do Celery indisponivel -> upload nao falha; row fica `erro`.

    Regressao do fix do Devin Review #2: AGENTS.md exige OCR no worker,
    mas se o broker estiver fora a UX nao pode quebrar -- o operador
    precisa ver o anexo persistido com mensagem de erro clara.
    """

    async def _broken(_parte_id: int) -> None:
        raise ConnectionError("broker offline")

    app.dependency_overrides[get_ocr_dispatcher] = lambda: _broken
    try:
        files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
        r = await api_client.post(
            "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["ocr_status"] == PARTE_ERRO
        assert "broker offline" in (body["ocr_error_msg"] or "")
        assert body["anexo_path"] is not None
    finally:
        app.dependency_overrides.pop(get_ocr_dispatcher, None)


# --- delete + cascade ------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_remove_anexo_do_storage(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    parte_id = r.json()["id"]
    assert any(in_memory_storage._data)

    rd = await api_client.delete(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}", headers=auth_headers
    )
    assert rd.status_code == 204
    assert not in_memory_storage._data

    # Audit_log delete registrado.
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "manutencao_frota.parte_diaria",
                    AuditLog.resource_id == parte_id,
                    AuditLog.action == "delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_veiculo_delete_nao_deleta_parte_diaria(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    """Auditoria: parte diaria sobrevive ao delete do veiculo (FK
    SET NULL). Cascata seria perda de evidencia operacional."""
    rv = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABC1234", "renavam": "12345678900"},
        headers=auth_headers,
    )
    veiculo_id = rv.json()["id"]
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    rp = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files, headers=auth_headers
    )
    parte_id = rp.json()["id"]
    assert rp.json()["veiculo_id"] == veiculo_id

    rd = await api_client.delete(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}", headers=auth_headers
    )
    assert rd.status_code == 204

    # Parte diaria continua existindo, mas com veiculo_id=NULL.
    parte = await db_session.get(ParteDiaria, parte_id)
    assert parte is not None
    assert parte.veiculo_id is None


# --- service direto: error path de read_anexo -----------------------------


@pytest.mark.asyncio
async def test_processar_ocr_falha_de_read_anexo(
    db_session: AsyncSession,
) -> None:
    """Anexo apagado/perdido -> ocr_status=erro com error_msg claro."""

    class BrokenStorage:
        async def read(self, storage_path: str) -> bytes:
            raise OSError("file not found")

    parte = ParteDiaria(
        anexo_path="mem://broken",
        filename_original="x.pdf",
        mime_type="application/pdf",
        ocr_status="pendente",
    )
    db_session.add(parte)
    await db_session.commit()
    await db_session.refresh(parte)

    parte = await service.processar_ocr_parte_diaria(
        db_session,
        parte.id,
        client=FakeOkOcrClient(),
        storage=BrokenStorage(),
    )
    assert parte.ocr_status == PARTE_ERRO
    assert "file not found" in (parte.ocr_error_msg or "")


# --- regressao Devin Review post-merge -------------------------------------


@pytest.mark.asyncio
async def test_celery_dispatcher_e_singleton() -> None:
    """Bug do Devin Review: cada `enqueue_ocr_parte_diaria` criava um
    novo `Celery(...)` -- vazava pool de conexoes Redis/AMQP. O fix
    cacheia em `_celery_dispatcher_singleton`.
    """
    service.reset_celery_dispatcher_singleton()
    a = service.get_celery_dispatcher()
    b = service.get_celery_dispatcher()
    assert a is b, "dispatcher Celery deve ser singleton (sem leak de pool)"

    # E `enqueue_ocr_parte_diaria` deve usar o mesmo singleton.
    sent: list[tuple[str, list, str]] = []

    class _Spy:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args or [], queue or ""))

    spy = _Spy()
    # Substitui o singleton em memoria pelo spy.
    service._celery_dispatcher_singleton = spy  # type: ignore[attr-defined]
    try:
        service.enqueue_ocr_parte_diaria(42)
        service.enqueue_ocr_parte_diaria(43)
        assert len(sent) == 2
        assert sent[0] == ("worker.tasks.manutencao.ocr_parte_diaria", [42], "manutencao")
        assert sent[1] == ("worker.tasks.manutencao.ocr_parte_diaria", [43], "manutencao")
    finally:
        service.reset_celery_dispatcher_singleton()


@pytest.mark.asyncio
async def test_open_partes_diarias_storage_local(tmp_path) -> None:
    """`open_partes_diarias_storage` (helper compartilhado API/worker)
    deve devolver `LocalStorage` quando `STORAGE_BACKEND` != onedrive.

    Regressao do Devin Review: API saving em OneDrive / worker lendo com
    LocalStorage -> FileNotFoundError. Helper unico evita divergencia.
    """
    from types import SimpleNamespace

    from app.modules.licitacoes.storage import LocalStorage

    settings = SimpleNamespace(
        storage_backend="local",
        editais_storage_path=str(tmp_path / "editais"),
        parte_diaria_storage_subdir="partes_diarias",
    )
    async with service.open_partes_diarias_storage(settings) as storage:
        assert isinstance(storage, LocalStorage)


@pytest.mark.asyncio
async def test_open_partes_diarias_storage_onedrive_aclose() -> None:
    """No backend OneDrive o helper deve abrir client httpx e fechar no
    finally -- senao o worker vaza pool TCP a cada parte diaria."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    settings = SimpleNamespace(
        storage_backend="onedrive",
        editais_storage_path="/tmp/editais",
        parte_diaria_storage_subdir="partes_diarias",
        ms_graph_tenant_id="t",
        ms_graph_client_id="c",
        ms_graph_client_secret="s",
        ms_graph_drive_id="d",
    )
    fake_client = AsyncMock()
    fake_client.aclose = AsyncMock()
    with patch(
        "app.integrations.onedrive.client.build_onedrive_client",
        return_value=fake_client,
    ):
        async with service.open_partes_diarias_storage(settings) as storage:
            assert storage is not None
        fake_client.aclose.assert_awaited_once()


# --- D5 fase 2: apontamento manual via PWA mobile -------------------------


@pytest.mark.asyncio
async def test_manual_post_cria_parte_revisada_sem_ocr(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """PWA envia JSON estruturado -- row sai como revisado/manual_pwa,
    sem dispatcher OCR, sem anexo. Caracteristica do D5 fase 2."""
    payload = {
        "client_uuid": "11111111-1111-1111-1111-111111111111",
        "data": "2025-08-15",
        "operador": "Joao da Silva",
        "obra": "Obra Norte",
        "horimetro_inicio": "1234.50",
        "horimetro_fim": "1278.90",
        "km_inicio": 45000,
        "km_fim": 45230,
        "combustivel_litros": "78.50",
        "combustivel_custo": "450.00",
        "observacoes": "vibracao no motor",
    }
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ocr_status"] == PARTE_REVISADO
    assert body["ocr_source"] == "manual_pwa"
    assert body["anexo_path"] is None
    assert body["filename_original"] is None
    assert body["operador"] == "Joao da Silva"
    assert float(body["combustivel_litros"]) == 78.5
    assert body["client_uuid"] == "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_manual_post_idempotente_via_client_uuid(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Reenvio com mesmo client_uuid devolve a row original (200 OK)
    -- garantia de idempotencia para a fila offline do PWA."""
    payload = {
        "client_uuid": "22222222-2222-2222-2222-222222222222",
        "obra": "Obra Sul",
        "horimetro_inicio": "100.00",
        "horimetro_fim": "108.00",
    }
    r1 = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r1.status_code == 201, r1.text
    parte_id_1 = r1.json()["id"]

    # Reenvio (PWA achou que falhou e retentou)
    r2 = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text  # nao 201, nao duplicou
    assert r2.json()["id"] == parte_id_1


@pytest.mark.asyncio
async def test_manual_post_rejeita_horimetro_fim_menor_que_inicio(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Inversao de horimetro indica erro de digitacao -- backend deve
    422 antes de gravar lixo no banco."""
    payload = {
        "horimetro_inicio": "1000.00",
        "horimetro_fim": "999.00",
    }
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text
    assert "horimetro" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_manual_post_rejeita_km_fim_menor_que_inicio(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "km_inicio": 50000,
        "km_fim": 49000,
    }
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text
    assert "km" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_manual_post_aceita_payload_parcial(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Apontador em area sem sinal pode salvar parcialmente -- horimetro
    so com inicio, sem fim, sem combustivel. Backend nao trava."""
    payload = {
        "obra": "Obra parcial",
        "horimetro_inicio": "500.00",
        # sem fim, sem km, sem combustivel
    }
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["horimetro_fim"] is None
    assert body["combustivel_litros"] is None


@pytest.mark.asyncio
async def test_manual_post_exige_auth(api_client: AsyncClient) -> None:
    """Sem JWT nao deixa entrar (LGPD: actor real no audit_log)."""
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json={"obra": "x"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_manual_post_grava_audit_log_com_actor(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """audit_log precisa ter actor=email do JWT -- nao 'system'."""
    payload = {
        "client_uuid": "33333333-3333-3333-3333-333333333333",
        "obra": "Obra audit",
    }
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r.status_code == 201
    parte_id = r.json()["id"]

    res = await db_session.execute(
        select(AuditLog)
        .where(AuditLog.resource == "manutencao_frota.parte_diaria")
        .where(AuditLog.resource_id == parte_id)
        .where(AuditLog.action == "create_manual")
    )
    rows = res.scalars().all()
    assert len(rows) == 1
    assert rows[0].actor != "system"
    assert "@" in rows[0].actor  # email real do JWT


# --- D5 fase 2: calculo de consumo + alerta manutencao --------------------


def test_calcular_consumo_dados_completos() -> None:
    """Caso feliz: horimetro + km + combustivel completos."""
    from decimal import Decimal as D

    parte = ParteDiaria(
        id=1,
        horimetro_inicio=D("100.00"),
        horimetro_fim=D("108.00"),  # 8h
        km_inicio=10000,
        km_fim=10240,  # 240km
        combustivel_litros=D("64.00"),
        combustivel_custo=D("400.00"),
    )
    consumo = service.calcular_consumo_parte_diaria(parte)
    assert consumo["horas_trabalhadas"] == D("8.00")
    assert consumo["km_rodados"] == 240
    assert consumo["consumo_litros_por_hora"] == D("8.000")  # 64/8
    assert consumo["consumo_km_por_litro"] == D("3.750")  # 240/64
    assert consumo["custo_por_hora"] == D("50.00")
    assert consumo["alerta_manutencao_preventiva"] is False


def test_calcular_consumo_sem_combustivel_zera_so_consumo() -> None:
    """Apontamento sem litros nao invalida horas_trabalhadas."""
    from decimal import Decimal as D

    parte = ParteDiaria(
        id=1, horimetro_inicio=D("100.00"), horimetro_fim=D("108.00")
    )
    consumo = service.calcular_consumo_parte_diaria(parte)
    assert consumo["horas_trabalhadas"] == D("8.00")
    assert consumo["consumo_litros_por_hora"] is None
    assert consumo["consumo_km_por_litro"] is None
    assert consumo["custo_por_hora"] is None


def test_calcular_consumo_zero_horas_nao_calcula_l_h() -> None:
    """horimetro_fim == inicio -> evitar divisao por zero."""
    from decimal import Decimal as D

    parte = ParteDiaria(
        id=1,
        horimetro_inicio=D("500.00"),
        horimetro_fim=D("500.00"),
        combustivel_litros=D("10.00"),
    )
    consumo = service.calcular_consumo_parte_diaria(parte)
    assert consumo["horas_trabalhadas"] is None
    assert consumo["consumo_litros_por_hora"] is None


def test_calcular_consumo_alerta_manutencao_atravessa_250h() -> None:
    """horimetro_anterior=240h, fim=260h -> cruzou 250h -> alerta."""
    from decimal import Decimal as D

    parte = ParteDiaria(
        id=1, horimetro_inicio=D("240.00"), horimetro_fim=D("260.00")
    )
    consumo = service.calcular_consumo_parte_diaria(
        parte, horimetro_anterior=D("240.00")
    )
    assert consumo["alerta_manutencao_preventiva"] is True


def test_calcular_consumo_sem_alerta_dentro_da_janela() -> None:
    """horimetro 240->249 -- ainda nao chegou no marco de 250h."""
    from decimal import Decimal as D

    parte = ParteDiaria(
        id=1, horimetro_inicio=D("240.00"), horimetro_fim=D("249.00")
    )
    consumo = service.calcular_consumo_parte_diaria(
        parte, horimetro_anterior=D("240.00")
    )
    assert consumo["alerta_manutencao_preventiva"] is False


@pytest.mark.asyncio
async def test_endpoint_consumo_devolve_metricas(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """E2E: cria parte manual, consulta /consumo, valida calculo."""
    payload = {
        "client_uuid": "44444444-4444-4444-4444-444444444444",
        "horimetro_inicio": "1000.00",
        "horimetro_fim": "1010.00",  # 10h
        "combustivel_litros": "70.00",  # 7l/h
    }
    r1 = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json=payload,
        headers=auth_headers,
    )
    assert r1.status_code == 201
    parte_id = r1.json()["id"]

    r2 = await api_client.get(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}/consumo"
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert float(body["horas_trabalhadas"]) == 10.0
    assert float(body["consumo_litros_por_hora"]) == 7.0
    assert body["alerta_manutencao_preventiva"] is False


@pytest.mark.asyncio
async def test_endpoint_consumo_404_se_inexistente(
    api_client: AsyncClient,
) -> None:
    r = await api_client.get(
        "/api/v1/manutencao-frota/partes-diarias/999999/consumo"
    )
    assert r.status_code == 404
