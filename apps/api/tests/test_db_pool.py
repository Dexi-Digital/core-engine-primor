"""`DB_NULLPOOL` desliga o pool -- usado pelo worker Celery.

Cada task do worker roda `asyncio.run`; conexao asyncpg devolvida ao
QueuePool fica presa ao loop que a abriu e a task seguinte no mesmo
processo quebra. Subprocesso para garantir import limpo de `app.core.db`.
"""
import os
import subprocess
import sys

CODIGO = "from app.core.db import engine; print(type(engine.pool).__name__)"


def _pool(**extra: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("DB_NULLPOOL", "VERCEL")}
    env.update(extra)
    result = subprocess.run(
        [sys.executable, "-c", CODIGO], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip().splitlines()[-1]


def test_db_nullpool_desliga_o_pool():
    assert _pool(DB_NULLPOOL="1") == "NullPool"


def test_sem_a_variavel_mantem_o_pool():
    assert _pool() != "NullPool"
