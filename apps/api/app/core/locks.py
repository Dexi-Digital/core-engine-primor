"""Lock distribuido single-flight sobre Redis.

Motivacao concreta (TOTVS RM, decisao #2): licenca de WebService do RM
e consumida POR REQUISICAO e so liberada quando a requisicao termina.
Duas execucoes sobrepostas do mesmo pull dobram o consumo de licenca e
podem esgotar o pool -- que escala ate a TOTVS Full e derruba usuario
de verdade do ERP. O lock garante que a segunda execucao desiste em vez
de concorrer.

Serve para qualquer job que nao pode rodar duas vezes ao mesmo tempo
(beat atrasado + disparo manual, worker reiniciando no meio, etc).
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Compare-and-delete atomico: so apaga a chave se o valor ainda for o
# NOSSO token. Sem isso, um pull que estourou o TTL apagaria, na saida,
# o lock que outro worker ja tinha adquirido.
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


@asynccontextmanager
async def single_flight(
    key: str, *, redis: Any, ttl_s: int
) -> AsyncIterator[bool]:
    """Adquire `key` no Redis; cede `True` se conseguiu, `False` se ja havia dono.

    O caller DECIDE o que fazer quando nao adquire -- normalmente logar
    e sair sem erro (a outra execucao ja esta fazendo o trabalho).

    `ttl_s` e a rede de seguranca para worker morto (OOM, deploy no meio
    do job): sem ele, o lock ficaria preso para sempre e o job nunca
    mais rodaria. Dimensione acima da duracao esperada do job.
    """
    token = uuid.uuid4().hex
    adquiriu = bool(await redis.set(key, token, nx=True, ex=ttl_s))
    if not adquiriu:
        logger.info("single_flight: %s ja esta em execucao, pulando", key)
    try:
        yield adquiriu
    finally:
        if adquiriu:
            try:
                await redis.eval(_RELEASE_LUA, 1, key, token)
            except Exception:  # noqa: BLE001
                # Falhar ao liberar nao pode mascarar a excecao do job.
                # O TTL cobre o vazamento.
                logger.warning("single_flight: falha ao liberar %s", key, exc_info=True)
