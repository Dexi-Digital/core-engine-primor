"""pg_trgm extension + GIN index on licitacoes.objeto_compra

Enables fuzzy/full-text-ish search on the licitacao `objeto_compra` column
using PostgreSQL's `pg_trgm` extension. The service layer combines `ilike`
filtering with `similarity()` ordering when running on Postgres; on SQLite
(tests / local dev without docker) we fall back to plain `ilike` and this
migration becomes a no-op.

Revision ID: 3fbc4a1e8d57
Revises: 2cd589981264
Create Date: 2026-04-23
"""
from __future__ import annotations

from alembic import op

revision = "3fbc4a1e8d57"
down_revision = "2cd589981264"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # pg_trgm is a Postgres-only extension; skip on SQLite/tests.
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_licitacoes_objeto_compra_trgm "
        "ON licitacoes USING gin (objeto_compra gin_trgm_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_licitacoes_objeto_compra_trgm")
    # Keep pg_trgm installed -- dropping extensions in downgrade is risky
    # because other objects in the DB may rely on it.
