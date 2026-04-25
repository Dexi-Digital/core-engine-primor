"""Adapta `OneDriveClient` para o `EditaisStorage` protocol (D.4).

Decisao de design:
  - `storage_path` opaco do `EditaisStorage` vira o `id` do item Graph.
    Assim, os modelos do D.4 (`AnexoEdital.storage_path`) nao precisam
    saber se o backend e local ou cloud -- a coluna ja e `String(1024)`.
  - `read()` baixa via /content; e o caminho usado pelo `pdf_extract`
    durante a analise IA (D.5) e durante o stream de download.
  - Falhas de IO viram `OSError` para casar com o que o caller ja trata
    (o loop de download em `editais.py` captura `OSError`).

Esta classe implementa exatamente o `Protocol EditaisStorage` definido
em `app/modules/licitacoes/storage.py`. Optei por manter o protocolo
atual sem alargar para um `DocumentStorage` generico para minimizar a
superficie deste PR -- quando o Modulo A precisar de upload de ASO,
generalizamos.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from app.integrations.onedrive.client import (
    OneDriveClient,
    OneDriveError,
    OneDriveItemNotFound,
    OneDriveMockClient,
)

logger = logging.getLogger(__name__)


_PATH_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(raw: str) -> str:
    """Normaliza um segmento de path para o Graph.

    Microsoft Graph aceita nomes razoaveis mas a documentacao desencoraja
    caracteres `# % & * { } / \\ : < > ? + |`. Trocamos o que nao e
    alfanumerico/`._-` por `_` e cortamos em 200 chars.
    """
    cleaned = _PATH_SAFE.sub("_", raw).strip("._") or "anexo"
    return cleaned[:200]


class OneDriveStorage:
    """Implementa `EditaisStorage` usando Microsoft Graph.

    O `storage_path` retornado e o `id` do item no drive -- e
    o que o caller persiste em `AnexoEdital.storage_path`.
    """

    def __init__(
        self, client: OneDriveClient | OneDriveMockClient
    ) -> None:
        self._client = client

    async def save(
        self,
        *,
        licitacao_id: int,
        filename: str,
        content: AsyncIterator[bytes],
    ) -> tuple[str, int]:
        relative = f"{licitacao_id}/{_safe_segment(filename)}"
        try:
            item = await self._client.upload(
                relative_path=relative, content=content
            )
        except OneDriveError as exc:
            raise OSError(f"OneDrive upload falhou: {exc}") from exc
        item_id = item.get("id")
        size = int(item.get("size") or 0)
        if not item_id:
            raise OSError("OneDrive upload retornou sem 'id'")
        return item_id, size

    async def read(self, storage_path: str) -> bytes:
        try:
            return await self._client.download(storage_path)
        except OneDriveItemNotFound as exc:
            raise FileNotFoundError(str(exc)) from exc
        except OneDriveError as exc:
            raise OSError(f"OneDrive download falhou: {exc}") from exc

    async def delete(self, storage_path: str) -> None:
        try:
            await self._client.delete(storage_path)
        except OneDriveItemNotFound:
            # idempotente: se ja foi deletado em outro lugar, OK.
            return
        except OneDriveError as exc:
            raise OSError(f"OneDrive delete falhou: {exc}") from exc
