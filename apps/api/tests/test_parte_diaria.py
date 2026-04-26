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
    get_documentai_dep,
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
        self.calls.append(
            {"size": len(content), "mime": mime_type, "filename": filename}
        )
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


@pytest.fixture
def fake_ok_ocr():
    client = FakeOkOcrClient()
    app.dependency_overrides[get_documentai_dep] = lambda: client
    yield client
    app.dependency_overrides.pop(get_documentai_dep, None)


@pytest.fixture
def fake_failing_ocr():
    client = FakeFailingOcrClient()
    app.dependency_overrides[get_documentai_dep] = lambda: client
    yield client
    app.dependency_overrides.pop(get_documentai_dep, None)


# --- endpoint POST /partes-diarias -----------------------------------------


@pytest.mark.asyncio
async def test_upload_extrai_campos_e_persiste(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
) -> None:
    files = {"arquivo": ("parte-001.pdf", b"%PDF-fake-bytes", "application/pdf")}
    r = await api_client.post("/api/v1/manutencao-frota/partes-diarias", files=files)
    assert r.status_code == 201, r.text
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
) -> None:
    files = {"arquivo": ("vazio.pdf", b"", "application/pdf")}
    r = await api_client.post("/api/v1/manutencao-frota/partes-diarias", files=files)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_amarra_veiculo_via_placa_extraida(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
) -> None:
    """Quando OCR extrai placa que existe no cadastro, parte_diaria.veiculo_id
    e setado automaticamente."""
    # Cria veiculo com mesma placa que o mock OCR retorna.
    rv = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABC1234", "renavam": "12345678900"},
    )
    veiculo_id = rv.json()["id"]

    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post("/api/v1/manutencao-frota/partes-diarias", files=files)
    assert r.status_code == 201
    assert r.json()["veiculo_id"] == veiculo_id


@pytest.mark.asyncio
async def test_ocr_falha_devolve_201_com_status_erro(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_failing_ocr: FakeFailingOcrClient,
) -> None:
    """Document AI fora do ar -> parte registrada com status='erro'.

    Endpoint NAO devolve 5xx -- UI renderiza erro inline e operador
    pode reprocessar."""
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post("/api/v1/manutencao-frota/partes-diarias", files=files)
    assert r.status_code == 201
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
) -> None:
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post("/api/v1/manutencao-frota/partes-diarias", files=files)
    assert r.status_code == 201
    parte_id = r.json()["id"]
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource == "manutencao_frota.parte_diaria",
                AuditLog.resource_id == parte_id,
            )
        )
    ).scalars().all()
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
) -> None:
    """Operador ajusta um campo -> status auto vira `revisado`."""
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files
    )
    parte_id = r.json()["id"]
    assert r.json()["ocr_status"] == PARTE_PROCESSADO

    r2 = await api_client.patch(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}",
        json={"operador": "Pedro Corrigido"},
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
) -> None:
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files
    )
    parte_id = r.json()["id"]

    r_bad = await api_client.patch(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}",
        json={"ocr_status": "fantasma"},
    )
    assert r_bad.status_code == 422


@pytest.mark.asyncio
async def test_patch_404_em_id_inexistente(
    api_client: AsyncClient,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
) -> None:
    r = await api_client.patch(
        "/api/v1/manutencao-frota/partes-diarias/999",
        json={"operador": "x"},
    )
    assert r.status_code == 404


# --- listagem + detalhe ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_filtra_por_status_e_obra(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
) -> None:
    # 2 uploads -> ambos com status=processado, obra=Obra Norte (mock).
    for fn in ("p1.pdf", "p2.pdf"):
        await api_client.post(
            "/api/v1/manutencao-frota/partes-diarias",
            files={"arquivo": (fn, b"%PDF-x", "application/pdf")},
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
) -> None:
    """Upload com OCR falhando -> patch override do client para OK ->
    reprocessar deve atualizar para `processado`."""
    failing = FakeFailingOcrClient()
    app.dependency_overrides[get_documentai_dep] = lambda: failing
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files
    )
    parte_id = r.json()["id"]
    assert r.json()["ocr_status"] == PARTE_ERRO

    # Substitui pelo OK e reprocessa.
    ok = FakeOkOcrClient()
    app.dependency_overrides[get_documentai_dep] = lambda: ok
    try:
        r2 = await api_client.post(
            f"/api/v1/manutencao-frota/partes-diarias/{parte_id}/reprocessar"
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["ocr_status"] == PARTE_PROCESSADO
        assert body["operador"] == "Joao da Silva"
    finally:
        app.dependency_overrides.pop(get_documentai_dep, None)


# --- delete + cascade ------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_remove_anexo_do_storage(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
) -> None:
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    r = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files
    )
    parte_id = r.json()["id"]
    assert any(in_memory_storage._data)

    rd = await api_client.delete(
        f"/api/v1/manutencao-frota/partes-diarias/{parte_id}"
    )
    assert rd.status_code == 204
    assert not in_memory_storage._data

    # Audit_log delete registrado.
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource == "manutencao_frota.parte_diaria",
                AuditLog.resource_id == parte_id,
                AuditLog.action == "delete",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_veiculo_delete_nao_deleta_parte_diaria(
    api_client: AsyncClient,
    db_session: AsyncSession,
    in_memory_storage: InMemoryStorage,
    fake_ok_ocr: FakeOkOcrClient,
) -> None:
    """Auditoria: parte diaria sobrevive ao delete do veiculo (FK
    SET NULL). Cascata seria perda de evidencia operacional."""
    rv = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": "ABC1234", "renavam": "12345678900"},
    )
    veiculo_id = rv.json()["id"]
    files = {"arquivo": ("parte.pdf", b"%PDF-x", "application/pdf")}
    rp = await api_client.post(
        "/api/v1/manutencao-frota/partes-diarias", files=files
    )
    parte_id = rp.json()["id"]
    assert rp.json()["veiculo_id"] == veiculo_id

    rd = await api_client.delete(
        f"/api/v1/manutencao-frota/veiculos/{veiculo_id}"
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
