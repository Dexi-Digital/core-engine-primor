"""O worker precisa ligar `DB_NULLPOOL` antes de qualquer import de `app`.

Cada task roda `asyncio.run` (loop novo) no mesmo processo; conexao
asyncpg devolvida ao QueuePool fica presa ao loop anterior e a task
seguinte quebra. O efeito da variavel no engine e testado na API
(`apps/api/tests/test_db_pool.py`).
"""
import os
import subprocess
import sys


def test_importar_worker_liga_nullpool():
    env = {k: v for k, v in os.environ.items() if k != "DB_NULLPOOL"}
    result = subprocess.run(
        [sys.executable, "-c", "import os, worker.main; print(os.environ.get('DB_NULLPOOL'))"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "1"
