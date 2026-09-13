"""Roda o pull SST da OnSafety (ASOs + fichas de EPI + treinamentos).

Mesma operacao do cron das 07h30, disparavel sob demanda:

    python -m scripts.pull_onsafety

Nao cria funcionario -- o matching e por CPF e quem nao casa entra nos
contadores `*_no_match` (ADR-001). Para popular o cadastro, use
`scripts.import_trabalhadores_onsafety` antes.

ATENCAO: com token de producao isso grava DADO DE SAUDE (ASO) no banco
apontado por `DATABASE_URL`. O script imprime o modo antes de rodar.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict

import structlog

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.integrations.onsafety.client import OnsafetyClient
from app.modules.dp_sesmt.onsafety_sync import pull_onsafety

logger = structlog.get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    client = OnsafetyClient(
        api_token=settings.onsafety_token,
        base_url=settings.onsafety_base_url,
    )
    modo = "MOCK" if client.is_mock else f"REAL ({settings.onsafety_base_url})"
    logger.info("pull_onsafety: starting", modo=modo)
    try:
        async with SessionLocal() as db:
            summary = await pull_onsafety(db, client)
    finally:
        await client.aclose()

    counts = asdict(summary)
    logger.info("pull_onsafety: done", **counts)
    print(
        "pull_onsafety ["
        + modo
        + "]: "
        + ", ".join(f"{k}={v}" for k, v in counts.items() if k != "source")
    )


if __name__ == "__main__":
    asyncio.run(main())
