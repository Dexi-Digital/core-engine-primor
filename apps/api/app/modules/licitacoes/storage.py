"""Storage abstraction for edital files (D.4).

Files are identified by an opaque `storage_path` string. The backend
decides whether that is a filesystem path, an S3 key, or anything else.
MVP ships with `LocalStorage` only (writes under a configurable root
directory); swap in a MinIO/S3 implementation when deploying.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol


class EditaisStorage(Protocol):
    """Minimal contract every storage backend must implement."""

    async def save(
        self,
        *,
        licitacao_id: int,
        filename: str,
        content: AsyncIterator[bytes],
    ) -> tuple[str, int]:
        """Persist `content` and return `(storage_path, size_bytes)`."""

    async def read(self, storage_path: str) -> bytes:  # pragma: no cover - not used by MVP router
        """Read back a previously stored file."""

    async def delete(self, storage_path: str) -> None:  # pragma: no cover - not used by MVP router
        """Remove a stored file (best-effort)."""

    async def ensure_project_folder(
        self, *, licitacao_id: int, nome_pasta: str
    ) -> tuple[str, str | None]:
        """Garante a pasta do projeto e retorna `(caminho, link_ou_None)`.

        `nome_pasta` e o slug humano de exibicao; o backend decide se
        consegue usa-lo fisicamente (o layout atual mantem a pasta
        fisica `{licitacao_id}` pela idempotencia do D.4).
        """


def _safe_filename(raw: str) -> str:
    """Normalize a user-supplied filename so it stays inside the target dir."""
    # Drop path separators, NULs and leading dots (hidden files).
    clean = raw.replace("\x00", "").replace("/", "_").replace("\\", "_").strip()
    clean = clean.lstrip(".") or "anexo.bin"
    return clean[:200]


class LocalStorage:
    """Writes files under `root / {licitacao_id} / {safe_filename}`.

    Streaming `save()` keeps memory bounded even for large projetos (50+ MB).
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    async def save(
        self,
        *,
        licitacao_id: int,
        filename: str,
        content: AsyncIterator[bytes],
    ) -> tuple[str, int]:
        subdir = self._root / str(licitacao_id)
        subdir.mkdir(parents=True, exist_ok=True)
        target = subdir / _safe_filename(filename)
        size = 0
        with target.open("wb") as fh:
            async for chunk in content:
                if not chunk:
                    continue
                fh.write(chunk)
                size += len(chunk)
        return str(target), size

    async def read(self, storage_path: str) -> bytes:
        path = Path(storage_path)
        return path.read_bytes()

    async def delete(self, storage_path: str) -> None:
        path = Path(storage_path)
        if path.exists():
            path.unlink()

    async def ensure_project_folder(
        self, *, licitacao_id: int, nome_pasta: str
    ) -> tuple[str, str | None]:
        # `nome_pasta` e so exibicao no backend local -- a pasta fisica
        # segue `{licitacao_id}`, onde o D.4 ja grava os anexos.
        subdir = self._root / str(licitacao_id)
        subdir.mkdir(parents=True, exist_ok=True)
        return str(subdir), None
