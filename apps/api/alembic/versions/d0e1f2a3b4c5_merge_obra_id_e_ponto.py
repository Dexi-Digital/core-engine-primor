"""merge: obra_id do documento (OnSafety) + ponto Solides/Tangerino

Duas cabecas independentes nascidas do mesmo pai (`e5ef0911decd`) e
mergeadas em main no mesmo dia:

- `c9d0e1f2a3b4`: coluna `obra_id` em `dp_employee_documents`.
- `a1b2c3d4e5f7`: tabelas do ponto eletronico.

Nao ha sobreposicao de tabela nem de coluna entre as duas, entao a
merge revision e vazia -- ela existe so para o `alembic upgrade head`
voltar a ter alvo unico. Sem ela o `preDeployCommand` do Railway
("alembic upgrade head") aborta com "Multiple head revisions" e o
deploy nao sobe. A suite de testes NAO pega isso: os testes criam o
schema com `Base.metadata.create_all`, nunca pelas migrations.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4, a1b2c3d4e5f7
Create Date: 2026-09-09
"""
from __future__ import annotations

revision = "d0e1f2a3b4c5"
down_revision = ("c9d0e1f2a3b4", "a1b2c3d4e5f7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
