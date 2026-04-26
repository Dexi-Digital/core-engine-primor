"""ORM model para sync do OneDrive (D1 fase 2).

Um `OneDriveSyncRun` grava uma execucao do sync: data inicio/fim,
quem disparou, pasta raiz varrida, quantos arquivos foram scanneados
e quantos docs resultaram criados/atualizados/ignorados. Erros ficam
em `summary_json["errors"]` (lista de `{path, reason}`) para nao
poluir a tabela com 1 row por arquivo.

As 4 tabelas de documentos (`dp_employee_documents`,
`frota_documentos`, `obras_documentos`, `empresa_documentos`) tem
FK nullable pra esta tabela via `onedrive_sync_run_id` -- assim
conseguimos auditar qual run criou/atualizou cada doc.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Status da execucao do sync.
RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_ERROR = "error"
RUN_STATUSES = frozenset({RUN_RUNNING, RUN_DONE, RUN_ERROR})

# Escopos aceitos -- filtra quais areas o sync atualiza. "all" varre
# todas as subpastas sob `root_folder`.
SCOPE_ALL = "all"
SCOPE_DP = "dp"
SCOPE_FROTA = "frota"
SCOPE_OBRAS = "obras"
SCOPE_EMPRESA = "empresa"
SCOPES_VALIDOS = frozenset(
    {SCOPE_ALL, SCOPE_DP, SCOPE_FROTA, SCOPE_OBRAS, SCOPE_EMPRESA}
)

# Valor gravado em `source` das rows criadas pelo sync -- distingue de
# uploads manuais (source='manual') e de eventuais integracoes futuras
# (directdata, dominio, etc.).
SOURCE_ONEDRIVE_SYNC = "onedrive_sync"


class OneDriveSyncRun(Base):
    __tablename__ = "onedrive_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=RUN_RUNNING, server_default=RUN_RUNNING
    )
    scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    root_folder: Mapped[str | None] = mapped_column(String(512), nullable=True)
    files_scanned: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    docs_created: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    docs_updated: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    docs_skipped: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    errors_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
