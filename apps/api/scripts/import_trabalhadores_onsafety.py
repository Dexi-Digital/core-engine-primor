"""Importa o cadastro de trabalhadores da OnSafety para `dp_employees`.

Operacao EXPLICITA e idempotente -- fora do pull diario de proposito
(ADR-001: o pull nunca cria funcionario). Rode quando decidir popular a
base com o cadastro real.

    python -m scripts.import_trabalhadores_onsafety

Precisa de `ONSAFETY_TOKEN` no ambiente; sem ele o adapter cai no mock
deterministico e planta os trabalhadores ficticios.

ATENCAO: com token de producao isso traz DADO PESSOAL REAL (nome, CPF,
matricula, obra) para o banco apontado por `DATABASE_URL`. Confira o
ambiente antes de rodar.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict

import structlog

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.integrations.onsafety.client import OnsafetyClient
from app.modules.dp_sesmt.onsafety_sync import import_trabalhadores

logger = structlog.get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    client = OnsafetyClient(
        api_token=settings.onsafety_token,
        base_url=settings.onsafety_base_url,
    )
    modo = "MOCK" if client.is_mock else f"REAL ({settings.onsafety_base_url})"
    logger.info("import_trabalhadores: starting", modo=modo)
    try:
        async with SessionLocal() as db:
            summary = await import_trabalhadores(db, client)
    finally:
        await client.aclose()

    counts = asdict(summary)
    logger.info("import_trabalhadores: done", **counts)
    print(
        "import_trabalhadores ["
        + modo
        + "]: "
        + ", ".join(f"{k}={v}" for k, v in counts.items() if k != "source")
    )


if __name__ == "__main__":
    asyncio.run(main())
