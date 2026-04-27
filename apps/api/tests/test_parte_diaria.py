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

    async def _dispatch(parte_id: int, actor: str) -> None:
        await service.processar_ocr_parte_diaria(
            db_session,
            parte_id,
            client=client,
            storage=storage,
            actor=actor,
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

    async def _broken(_parte_id: int, _actor: str) -> None:
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
        # Sem actor -- preserva compat (args curtos, worker cai em SYSTEM_WORKER).
        service.enqueue_ocr_parte_diaria(42)
        # Com actor -- propaga email do uploader nos args.
        service.enqueue_ocr_parte_diaria(43, actor="ana@primor.com")
        assert len(sent) == 2
        assert sent[0] == ("worker.tasks.manutencao.ocr_parte_diaria", [42], "manutencao")
        assert sent[1] == (
            "worker.tasks.manutencao.ocr_parte_diaria",
            [43, "ana@primor.com"],
            "manutencao",
        )
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


@pytest.mark.asyncio
async def test_consumo_ignora_horimetro_de_partes_pendentes_ou_erro(
    db_session: AsyncSession,
) -> None:
    """Regressao do finding Devin Review #24: a query do horimetro
    anterior precisa filtrar por ocr_status revisado/processado --
    senao uma parte pendente (com horimetro_fim cru de OCR ainda
    nao validado) seria usada como base do gatilho de 250h e
    geraria alerta espurio (ou perderia um real)."""
    from datetime import date as date_cls
    from decimal import Decimal as D

    from app.modules.manutencao_frota.models import PARTE_PENDENTE

    veiculo_id = 7777

    # Parte ANTIGA, status=pendente, horimetro_fim=140 (lixo de OCR
    # ainda nao validado). Nao deve influenciar o gatilho.
    pendente = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 10),
        horimetro_inicio=D("100.00"),
        horimetro_fim=D("140.00"),
        ocr_status=PARTE_PENDENTE,
        ocr_source="upload",
    )
    db_session.add(pendente)

    # Parte ANTIGA, status=revisado, horimetro_fim=240. Esta sim
    # deve ser usada como horimetro_anterior.
    revisada = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 12),
        horimetro_inicio=D("200.00"),
        horimetro_fim=D("240.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(revisada)

    # Parte ATUAL, horimetro_fim=260 -- atravessa marco de 250h
    # SE comparada com 240 (revisada). Se o filtro estivesse
    # quebrado, comparariamos com 140 (pendente) e ignorariamos
    # incorretamente o gatilho (140//250=0, 260//250=1, ainda
    # detectaria mas pelo motivo errado; e em outros cenarios a
    # parte pendente teria horimetro espurio que MASCARARIA o
    # cruzamento real).
    atual = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 15),
        horimetro_inicio=D("240.00"),
        horimetro_fim=D("260.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(atual)
    await db_session.commit()
    await db_session.refresh(atual)

    consumo = await service.get_consumo_parte_diaria(db_session, atual.id)
    assert consumo is not None
    # Alerta dispara comparando com 240 (revisada) -- nao com 140
    # (pendente, que seria ignorada).
    assert consumo["alerta_manutencao_preventiva"] is True


@pytest.mark.asyncio
async def test_manual_post_idempotente_em_race_toctou(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressao do finding Devin Review #24: simula race entre o
    fast-path (find_by_uuid) e o commit. Se uma requisicao concorrente
    inserir entre os dois, o IntegrityError do unique deve ser
    capturado e tratado como idempotente (devolve a row existente).

    SQLite nao enforça o partial unique do PG (o `postgresql_where`
    da migration so se aplica em PG), entao mockamos `db.commit` pra
    levantar IntegrityError UMA VEZ -- simulando o cenario PG real
    em que duas tabs do PWA drenam a mesma fila e a primeira ja
    inseriu antes da segunda commitar."""
    from datetime import date as date_cls

    from sqlalchemy.exc import IntegrityError

    from app.modules.manutencao_frota import service as parte_service
    from app.modules.manutencao_frota.models import PARTE_REVISADO

    uuid = "race-toctou-1234"

    # Row "vencedora" do race -- a que ja existe no banco quando
    # o commit da nossa requisicao falha.
    vencedora = ParteDiaria(
        veiculo_id=None,
        data=date_cls(2025, 9, 1),
        operador="concorrente",
        client_uuid=uuid,
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(vencedora)
    await db_session.commit()
    await db_session.refresh(vencedora)
    vencedora_id = vencedora.id

    # Fast-path miss: simulamos que NESTA leitura (antes do commit)
    # a row concorrente ainda nao existia para esta sessao. Forca o
    # codigo a tentar inserir.
    real_find = parte_service.find_parte_diaria_by_client_uuid
    call_count = {"n": 0}

    async def fake_find(db: AsyncSession, cu: str) -> ParteDiaria | None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # fast-path: nao acha
        return await real_find(db, cu)  # post-rollback: acha

    monkeypatch.setattr(
        parte_service, "find_parte_diaria_by_client_uuid", fake_find
    )

    # Forca commit a levantar IntegrityError UMA vez -- simulando
    # o unique-constraint do PG quando a request concorrente
    # comitou antes. Segunda chamada (apos rollback) e dispensavel
    # mas o handler nao tenta de novo de qualquer forma.
    real_commit = db_session.commit
    commit_calls = {"n": 0}

    async def fake_commit() -> None:
        commit_calls["n"] += 1
        if commit_calls["n"] == 1:
            raise IntegrityError("simulated", {}, Exception("unique violation"))
        await real_commit()

    monkeypatch.setattr(db_session, "commit", fake_commit)

    parte, criada = await parte_service.create_parte_diaria_manual(
        db_session,
        payload={
            "data": date_cls(2025, 9, 1),
            "operador": "duplicado",
            "client_uuid": uuid,
        },
        actor="test@primor.com",
    )

    assert criada is False
    assert parte.id == vencedora_id
    assert parte.operador == "concorrente"
    assert call_count["n"] == 2  # fast-path miss + post-rollback re-find
    assert commit_calls["n"] == 1  # so a tentativa que falhou


@pytest.mark.asyncio
async def test_manual_post_veiculo_inexistente_devolve_422(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Regressao do finding Devin Review #24: PWA com cache stale
    enviando veiculo_id que ja foi removido nao pode cair no
    try/except IntegrityError do client_uuid e voltar 500. Tem que
    validar a FK antes do INSERT e devolver 422 explicando."""
    res = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json={
            "data": "2025-09-15",
            "veiculo_id": 999_999,  # nao existe
            "operador": "joao",
        },
        headers=auth_headers,
    )
    assert res.status_code == 422, res.text
    assert "999999" in res.json()["detail"] or "nao existe" in res.json()["detail"]


@pytest.mark.asyncio
async def test_manual_post_validacao_horimetro_devolve_422(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Regressao: service agora levanta ValueError (nao
    HTTPException) seguindo convencao do modulo. Router precisa
    converter em 422 corretamente."""
    res = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json={
            "data": "2025-09-15",
            "horimetro_inicio": "100.00",
            "horimetro_fim": "50.00",  # menor -- invalido
        },
        headers=auth_headers,
    )
    assert res.status_code == 422, res.text
    assert "horimetro" in res.json()["detail"]


@pytest.mark.asyncio
async def test_consumo_tiebreaker_por_id_quando_mesma_data(
    db_session: AsyncSession,
) -> None:
    """Regressao do finding Devin Review #24: quando ha varias
    partes na mesma data (ex.: turno manha + tarde), o ORDER BY
    sem tiebreaker pode escolher qualquer uma -- afeta o gatilho
    de 250h. Garantimos que escolhe a de id maior (mais recente
    no insert order, proxy razoavel para 'mais recente no tempo')."""
    from datetime import date as date_cls
    from decimal import Decimal as D

    veiculo_id = 8888

    # Dois apontamentos na MESMA data, horimetros diferentes (turno
    # da manha e tarde, ambos revisados). Sem tiebreaker o DB pode
    # devolver qualquer um.
    manha = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 14),
        horimetro_inicio=D("220.00"),
        horimetro_fim=D("240.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(manha)
    await db_session.commit()
    await db_session.refresh(manha)

    tarde = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 14),
        horimetro_inicio=D("240.00"),
        horimetro_fim=D("260.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(tarde)
    await db_session.commit()
    await db_session.refresh(tarde)

    # Parte ATUAL no dia seguinte, horimetro=270. Se o tiebreaker
    # escolhesse `manha` (240), o gatilho de 250h dispararia
    # (240//250=0, 270//250=1). Com `tarde` (260) NAO dispara
    # (260//250=1, 270//250=1). Garantimos que escolhe a tarde
    # (id maior).
    atual = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 15),
        horimetro_inicio=D("260.00"),
        horimetro_fim=D("270.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(atual)
    await db_session.commit()
    await db_session.refresh(atual)

    consumo = await service.get_consumo_parte_diaria(db_session, atual.id)
    assert consumo is not None
    # Tarde (id maior) escolhida -- nao cruza marco de 250h.
    assert consumo["alerta_manutencao_preventiva"] is False


@pytest.mark.asyncio
async def test_manual_post_client_uuid_string_vazia_tratada_como_ausente(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Regressao do finding Devin Review #24: client_uuid="" passa
    pela validacao Pydantic mas e gravado como NOT NULL no PG. Em
    qualquer segundo POST com "", o unique parcial dispara
    IntegrityError e o recovery (`if client_uuid:`) e falsy --
    deveria voltar 500. Service deve normalizar "" -> None antes
    do flow para evitar o modo de falha."""
    res1 = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json={"data": "2025-09-20", "operador": "primeiro", "client_uuid": ""},
        headers=auth_headers,
    )
    assert res1.status_code == 201, res1.text

    # Segundo POST com mesmo client_uuid="": antes do fix isso
    # quebrava com 500 no IntegrityError. Apos o fix "" e tratado
    # como None, ambos POSTs criam rows distintas (sem dedup).
    res2 = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias/manual",
        json={"data": "2025-09-20", "operador": "segundo", "client_uuid": ""},
        headers=auth_headers,
    )
    assert res2.status_code == 201, res2.text
    assert res2.json()["id"] != res1.json()["id"]


@pytest.mark.asyncio
async def test_consumo_inclui_predecessora_no_mesmo_dia(
    db_session: AsyncSession,
) -> None:
    """Regressao do finding Devin Review #24: turno manha + tarde
    no MESMO dia. Tarde precisa achar a manha como predecessora;
    senao volta pro dia anterior e gatilho de 250h dispara duas
    vezes (uma na manha, outra de novo na tarde porque ela compara
    com horimetro de 2 dias atras).

    Cenario: Aug 13 = 220, Aug 14 manha = 250 (cruza marco), Aug 14
    tarde = 260. Tarde precisa achar manha (250) como predecessora,
    nao Aug 13 (220). Com manha (250) como base: 250//250=1,
    260//250=1 -> nao dispara. Com Aug 13 (220): 220//250=0,
    260//250=1 -> dispara espurio."""
    from datetime import date as date_cls
    from decimal import Decimal as D

    veiculo_id = 7777

    # Aug 13: dia anterior, horimetro=220.
    aug13 = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 13),
        horimetro_inicio=D("200.00"),
        horimetro_fim=D("220.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(aug13)
    await db_session.commit()
    await db_session.refresh(aug13)

    # Aug 14 manha: cruza marco de 250h (220 -> 250).
    manha = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 14),
        horimetro_inicio=D("220.00"),
        horimetro_fim=D("250.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(manha)
    await db_session.commit()
    await db_session.refresh(manha)

    # Aug 14 tarde: 250 -> 260. Mesmo dia. Predecessora deve ser
    # a manha (id != tarde.id), nao Aug 13.
    tarde = ParteDiaria(
        veiculo_id=veiculo_id,
        data=date_cls(2025, 8, 14),
        horimetro_inicio=D("250.00"),
        horimetro_fim=D("260.00"),
        ocr_status=PARTE_REVISADO,
        ocr_source="manual_pwa",
    )
    db_session.add(tarde)
    await db_session.commit()
    await db_session.refresh(tarde)

    # Manha: cruza marco de 250 -- alerta correto.
    consumo_manha = await service.get_consumo_parte_diaria(db_session, manha.id)
    assert consumo_manha is not None
    assert consumo_manha["alerta_manutencao_preventiva"] is True

    # Tarde: predecessora deve ser manha (250). 250//250=1,
    # 260//250=1 -> NAO cruza marco de novo. Sem o fix, predecessora
    # seria Aug 13 (220). 220//250=0, 260//250=1 -> alerta espurio.
    consumo_tarde = await service.get_consumo_parte_diaria(db_session, tarde.id)
    assert consumo_tarde is not None
    assert consumo_tarde["alerta_manutencao_preventiva"] is False


# --- Wire current_user nos workers Celery ----------------------------------


@pytest.mark.asyncio
async def test_upload_ocr_audit_tem_email_do_uploader(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
    auth_headers: dict[str, str],
) -> None:
    """Regressao: o audit_log do OCR async gravava `actor='system'`
    sempre, mesmo quando um uploader humano disparava o upload via
    HTTP. Agora o router propaga `current_user.email` pro dispatcher
    -> Celery args -> service, e o audit traz o email real.
    """
    files = {"arquivo": ("parte-ocr-audit.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias",
        files=files,
        headers=auth_headers,
    )
    assert r.status_code == 202, r.text
    parte_id = r.json()["id"]

    # A audit "update" (stage=ocr_processado) deve ter o email do JWT.
    res = await db_session.execute(
        select(AuditLog)
        .where(AuditLog.resource == "manutencao_frota.parte_diaria")
        .where(AuditLog.resource_id == str(parte_id))
        .where(AuditLog.action == "update")
    )
    rows = res.scalars().all()
    # Deve ter ao menos um update (ocr_processado); pode ter extras se
    # o teste incluir pre-fill manual. Qualquer um que tenha stage
    # ocr_processado deve carregar o email.
    ocr_rows = [
        row for row in rows
        if row.metadata_json and "ocr_processado" in row.metadata_json
    ]
    assert ocr_rows, f"nenhum audit de ocr_processado encontrado: {rows}"
    for row in ocr_rows:
        assert row.actor != "system", (
            f"OCR audit com actor='system' (deveria ser email do uploader): {row}"
        )
        assert "@" in row.actor


@pytest.mark.asyncio
async def test_reprocessar_ocr_audit_tem_email_do_solicitante(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    auth_headers: dict[str, str],
) -> None:
    """Regressao: endpoint `/reprocessar` tambem passa actor.

    Faz upload com OCR falhando, reprocessa com OCR OK, checa que o
    audit do reprocesso (action=update com stage ocr_processado) tem
    email e nao bare 'system'.
    """
    # Upload inicial com OCR falhando.
    failing = FakeFailingOcrClient()
    app.dependency_overrides[get_ocr_dispatcher] = _eager_factory(
        failing, db_session, in_memory_storage
    )
    files = {"arquivo": ("reproc.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias",
        files=files,
        headers=auth_headers,
    )
    assert r.status_code == 202
    parte_id = r.json()["id"]

    # Swap pelo OK e reprocessa.
    ok = FakeOkOcrClient()
    app.dependency_overrides[get_ocr_dispatcher] = _eager_factory(
        ok, db_session, in_memory_storage
    )
    try:
        r2 = await api_client.post(
            f"/api/v1/manutencao-frota/partes-diarias/{parte_id}/reprocessar",
            headers=auth_headers,
        )
        assert r2.status_code == 202
    finally:
        app.dependency_overrides.pop(get_ocr_dispatcher, None)

    res = await db_session.execute(
        select(AuditLog)
        .where(AuditLog.resource == "manutencao_frota.parte_diaria")
        .where(AuditLog.resource_id == str(parte_id))
    )
    rows = res.scalars().all()
    # Todas as rows de audit dessa parte ja nasceram com email (upload
    # inicial + pre-fill + erro OCR + update pos reprocessamento).
    # Se um unico caiu em 'system', o wiring esta incompleto.
    assert rows, "sem audit rows"
    systems = [r for r in rows if r.actor == "system"]
    assert not systems, (
        f"audit rows com actor='system' (deveriam ter email): {systems}"
    )


@pytest.mark.asyncio
async def test_enqueue_ocr_parte_diaria_propaga_actor() -> None:
    """Regressao: `enqueue_ocr_parte_diaria(parte_id, actor=...)`
    deve incluir `actor` nos args do send_task quando informado.
    Sem actor, preserva args curtos (compat com fila pre-deploy).
    """
    service.reset_celery_dispatcher_singleton()
    sent: list[tuple[str, list, str]] = []

    class _Spy:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args or [], queue or ""))

    service._celery_dispatcher_singleton = _Spy()  # type: ignore[attr-defined]
    try:
        service.enqueue_ocr_parte_diaria(100)
        service.enqueue_ocr_parte_diaria(101, actor="bob@primor.com")
        service.enqueue_ocr_parte_diaria(102, actor=None)  # None == sem actor
        assert sent == [
            ("worker.tasks.manutencao.ocr_parte_diaria", [100], "manutencao"),
            (
                "worker.tasks.manutencao.ocr_parte_diaria",
                [101, "bob@primor.com"],
                "manutencao",
            ),
            ("worker.tasks.manutencao.ocr_parte_diaria", [102], "manutencao"),
        ]
    finally:
        service.reset_celery_dispatcher_singleton()


def test_audit_actor_constants_valores_canonicos() -> None:
    """Invariante: os 3 identificadores canonicos tem valores estaveis.

    Dashboards e queries podem filtrar por valor literal; mudar exige
    migration consciente.
    """
    from app.audit import actors

    assert actors.SYSTEM == "system"
    assert actors.SYSTEM_BEAT == "system:beat"
    assert actors.SYSTEM_WORKER == "system:worker"
