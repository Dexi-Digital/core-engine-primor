"""Middleware de observability.

`CorrelationIdMiddleware` garante que toda request tenha um
`correlation_id` (X-Correlation-ID) -- recebido do cliente quando
fornecido, gerado caso contrario. O ID e:

1. Anexado ao `request.state.correlation_id` para uso direto em
   handlers que queiram ler.
2. Bound no `structlog.contextvars` durante a request -- todos os
   `logger.info(...)` chamados dentro do handler/service ganham o
   campo `correlation_id` automaticamente, sem precisar threadear o
   parametro adiante.
3. Propagado de volta via header `X-Correlation-ID` na resposta para
   o cliente correlacionar no lado dele.

Quando uma task Celery e disparada dentro do request, o
`before_task_publish` em `apps/workers/worker/main.py` le o
contextvar e enfileira o ID no header da mensagem; o `task_prerun` no
worker refaz o bind. Assim a chain inteira (request HTTP -> task ->
sub-tasks) compartilha o mesmo `correlation_id`.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"


def _generate_correlation_id() -> str:
    """UUID4 sem hifens -- 32 hex chars compactos para logs."""
    return uuid.uuid4().hex


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Garante 1 correlation_id por request.

    Aceita o header `X-Correlation-ID` enviado pelo cliente (mesma
    chave usada pela maioria de proxies/CDNs). Se nao vier, gera UUID4
    hex de 32 chars. Repassa o valor de volta na response e bind nos
    contextvars do structlog para os logs do handler.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(CORRELATION_HEADER, "").strip()
        # Header sanity: aceitamos so chars seguros (hex/uuid/letters)
        # e limitamos tamanho para nao deixar payload arbitrario do
        # cliente vazar pra log infra (Datadog, Loki etc.).
        if not incoming or not _safe_correlation_value(incoming):
            correlation_id = _generate_correlation_id()
        else:
            correlation_id = incoming[:64]

        request.state.correlation_id = correlation_id
        # `bind_contextvars` e thread-/task-local -- nao vaza entre
        # requests concorrentes mesmo no mesmo loop. Devolve tokens
        # para reset manual no `finally`; sem isso, a chave persistiria
        # no proximo request que reutilizar o mesmo task slot do uvicorn.
        tokens = structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id
        )
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.reset_contextvars(**tokens)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def _safe_correlation_value(value: str) -> bool:
    """Aceita apenas chars seguros para log/header.

    Bloqueia injecoes tipo `"\\nfake-line"` ou caracteres de controle
    que poderiam confundir parsers de log downstream.
    """
    if len(value) > 128:
        return False
    return all(c.isalnum() or c in "-_." for c in value)


__all__ = [
    "CORRELATION_HEADER",
    "CorrelationIdMiddleware",
]
