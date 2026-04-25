"""Tests para `OneDriveStorage` (adapter -> EditaisStorage protocol)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.integrations.onedrive.client import (
    OneDriveError,
    OneDriveItemNotFound,
    OneDriveMockClient,
)
from app.integrations.onedrive.storage import OneDriveStorage


async def _stream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


class _BrokenClient:
    """Substitui o client por algo que so levanta -- testa traducao de erros."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def upload(self, **kwargs):  # noqa: ANN003
        raise self._exc

    async def download(self, item_id: str):  # noqa: ARG002
        raise self._exc

    async def delete(self, item_id: str):  # noqa: ARG002
        raise self._exc


@pytest.mark.asyncio
async def test_save_returns_storage_path_and_size():
    storage = OneDriveStorage(OneDriveMockClient())
    storage_path, size = await storage.save(
        licitacao_id=123, filename="edital.pdf", content=_stream(b"PDF")
    )
    assert storage_path.startswith("mock-")
    assert size == 3


@pytest.mark.asyncio
async def test_save_normalizes_filename_special_chars():
    """Arquivos com caracteres exoticos nao quebram o path do Graph."""
    storage = OneDriveStorage(OneDriveMockClient())
    sp1, _ = await storage.save(
        licitacao_id=1, filename="planta hidráulica #2.pdf", content=_stream(b"a")
    )
    sp2, _ = await storage.save(
        licitacao_id=1, filename="planta-hidraulica-2.pdf", content=_stream(b"a")
    )
    # Mesmo licitacao_id, paths normalizados devem dar ids estaveis.
    assert sp1.startswith("mock-")
    assert sp2.startswith("mock-")


@pytest.mark.asyncio
async def test_read_returns_uploaded_bytes():
    storage = OneDriveStorage(OneDriveMockClient())
    sp, _ = await storage.save(
        licitacao_id=7, filename="x.pdf", content=_stream(b"hello")
    )
    got = await storage.read(sp)
    assert got == b"hello"


@pytest.mark.asyncio
async def test_read_missing_raises_filenotfound():
    """O caller (`pdf_extract`) trata `FileNotFoundError` como anexo
    indisponivel; a OneDriveStorage TEM que mapear o 404 do Graph para
    isso, senao a analise IA quebra inteira em um anexo apagado."""
    storage = OneDriveStorage(OneDriveMockClient())
    with pytest.raises(FileNotFoundError):
        await storage.read("mock-does-not-exist")


@pytest.mark.asyncio
async def test_delete_idempotent_on_404():
    """Re-clicar 'remover' nao pode 500 -- vai virar reclamacao do user."""
    storage = OneDriveStorage(OneDriveMockClient())
    sp, _ = await storage.save(
        licitacao_id=2, filename="a.pdf", content=_stream(b"a")
    )
    await storage.delete(sp)
    # Segundo delete: nao levanta.
    await storage.delete(sp)


@pytest.mark.asyncio
async def test_save_translates_onedrive_error_to_oserror():
    """O loop de download em `editais.py` captura `OSError`; se a gente
    deixar `OneDriveError` vazar, um anexo com falha aborta o batch."""
    broken = _BrokenClient(OneDriveError("boom"))
    storage = OneDriveStorage(broken)  # type: ignore[arg-type]
    with pytest.raises(OSError, match="boom"):
        await storage.save(licitacao_id=1, filename="x.pdf", content=_stream(b"a"))


@pytest.mark.asyncio
async def test_read_translates_onedrive_error_to_oserror():
    broken = _BrokenClient(OneDriveError("network"))
    storage = OneDriveStorage(broken)  # type: ignore[arg-type]
    with pytest.raises(OSError, match="network"):
        await storage.read("anything")


@pytest.mark.asyncio
async def test_save_raises_when_item_has_no_id():
    """Se o Graph retornar 200 sem 'id' (nao deveria, mas defensivo)."""

    class _NoIdClient:
        async def upload(self, **kwargs):  # noqa: ANN003
            return {"name": "x.pdf", "size": 0}  # sem 'id'

        async def download(self, item_id: str):  # noqa: ARG002
            raise OneDriveItemNotFound("never called")

        async def delete(self, item_id: str):  # noqa: ARG002
            raise OneDriveItemNotFound("never called")

    storage = OneDriveStorage(_NoIdClient())  # type: ignore[arg-type]
    with pytest.raises(OSError, match="sem 'id'"):
        await storage.save(licitacao_id=1, filename="x.pdf", content=_stream(b"a"))
