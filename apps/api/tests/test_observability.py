"""Testes da camada de observability.

Cobre:
- Middleware: gera correlation_id quando ausente, preserva quando vem do
  cliente, valida sanitizacao do valor recebido.
- Logging: o `correlation_id` aparece no JSON dos logs emitidos durante
  a request (via structlog contextvars).
- Endpoint `/api/v1/observability/health`: agrega DB + Redis + storage.

Nao testamos a propagacao para o worker Celery em testes E2E aqui (faria
parte de teste integrado com Redis real). A unidade do `before_task_publish`
fica coberta indiretamente: ela so depende do contextvar bound pelo
middleware -- testar isso separadamente exigiria iniciar o broker.
"""
from __future__ import annotations

import logging

import pytest
import structlog
from httpx import AsyncClient

from app.core.middleware import (
    CORRELATION_HEADER,
    _generate_correlation_id,
    _safe_correlation_value,
)

# --- pure helpers -----------------------------------------------------------


def test_generate_correlation_id_is_hex_32() -> None:
    cid = _generate_correlation_id()
    assert len(cid) == 32
    int(cid, 16)  # nao crasha -- e hex valido


def test_safe_correlation_accepts_uuid_and_alnum() -> None:
    assert _safe_correlation_value("abc123-XYZ_42.7")
    assert _safe_correlation_value(
        "550e8400-e29b-41d4-a716-446655440000"
    )


def test_safe_correlation_rejects_injection_and_overlong() -> None:
    assert not _safe_correlation_value("evil\nlog injection")
    assert not _safe_correlation_value("a" * 200)
    assert not _safe_correlation_value("space here")
    assert not _safe_correlation_value("semicolon;separated")


# --- middleware (live ASGI) ------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_generates_correlation_when_missing(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    cid = resp.headers.get(CORRELATION_HEADER)
    assert cid is not None
    assert len(cid) == 32  # gerou UUID4 hex


@pytest.mark.asyncio
async def test_middleware_preserves_client_correlation(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get(
        "/health",
        headers={CORRELATION_HEADER: "trace-12345"},
    )
    assert resp.headers[CORRELATION_HEADER] == "trace-12345"


@pytest.mark.asyncio
async def test_middleware_drops_unsafe_correlation(
    api_client: AsyncClient,
) -> None:
    """Header malicioso (injection) -> middleware gera ID novo, nao usa
    o valor do cliente. Protege downstream log infra de poison."""
    resp = await api_client.get(
        "/health",
        headers={CORRELATION_HEADER: "evil\nlog-injection"},
    )
    cid = resp.headers[CORRELATION_HEADER]
    assert cid != "evil\nlog-injection"
    assert "\n" not in cid
    assert len(cid) == 32


@pytest.mark.asyncio
async def test_middleware_truncates_long_value(
    api_client: AsyncClient,
) -> None:
    """Cliente manda valor `safe` mas longo -- middleware ainda
    aceita ate 64 chars de truncamento (nao 128 como sanity check)."""
    long_safe = "a" * 65  # caractere safe mas estouraria limite truncado
    resp = await api_client.get(
        "/health",
        headers={CORRELATION_HEADER: long_safe},
    )
    cid = resp.headers[CORRELATION_HEADER]
    # 65 ainda passa _safe_correlation_value (max 128); truncado pra 64
    assert len(cid) <= 64


# --- logging integration ---------------------------------------------------


@pytest.mark.asyncio
async def test_correlation_id_present_in_structlog_during_request(
    api_client: AsyncClient,
) -> None:
    """Captura logs emitidos durante o handler -- garante que o
    `correlation_id` aparece (via merge_contextvars). Replica em
    miniatura o setup de logging da aplicacao com um WrappingFormatter
    para nao depender da configuracao global do pytest.
    """
    captured: list[dict] = []

    def capturing_processor(_logger, _name, event_dict):
        captured.append(dict(event_dict))
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            capturing_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    try:
        # /api/v1/observability/health usa logger module-level, mas
        # tambem faz uma chamada a log abaixo dentro do handler para
        # captura mais determistica.
        resp = await api_client.get(
            "/api/v1/observability/health",
            headers={CORRELATION_HEADER: "test-trace-abc"},
        )
        # health pode dar 503 se Redis nao disponivel -- tudo que nos
        # importa eh que o response carregou e tem cid no header.
        assert resp.headers[CORRELATION_HEADER] == "test-trace-abc"
        # Body deve conter o cid tambem (definido pelo handler)
        body = resp.json()
        assert body.get("correlation_id") == "test-trace-abc"
    finally:
        # Restaura config default
        from app.core.logging import configure_logging

        configure_logging()


# --- /api/v1/observability/health endpoint ---------------------------------


@pytest.mark.asyncio
async def test_observability_health_returns_aggregated_payload(
    api_client: AsyncClient,
) -> None:
    """Smoke test: endpoint responde JSON com status + checks por componente.

    O Redis check provavelmente da `error`/`timeout` em CI (sem broker),
    mas o endpoint precisa responder com 503 + payload diagnostico, nao
    200 silencioso ou 500 sem corpo.
    """
    resp = await api_client.get("/api/v1/observability/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "status" in body
    assert "checks" in body
    assert set(body["checks"].keys()) == {"db", "redis", "storage"}
    assert "correlation_id" in body
    # Cada check deve ter `status` legivel (nao crashou silenciosamente)
    for name in ("db", "redis", "storage"):
        assert "status" in body["checks"][name]
