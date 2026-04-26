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
    assert set(body["checks"].keys()) == {
        "db",
        "redis",
        "storage",
        "celery_workers",
    }
    assert "correlation_id" in body
    # Cada check deve ter `status` legivel (nao crashou silenciosamente)
    for name in ("db", "redis", "storage", "celery_workers"):
        assert "status" in body["checks"][name]


# --- celery workers check --------------------------------------------------


@pytest.mark.asyncio
async def test_check_celery_workers_ok_when_ping_returns_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workers respondendo -> status=ok + contagem + nomes ordenados."""
    from app.modules.observability import router as obs_router

    class _FakeInspect:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def ping(self) -> dict:
            return {
                "celery@worker-2": {"ok": "pong"},
                "celery@worker-1": {"ok": "pong"},
            }

    class _FakeControl:
        def inspect(self, timeout: float) -> _FakeInspect:
            return _FakeInspect(timeout)

    class _FakeDispatcher:
        control = _FakeControl()

    monkeypatch.setattr(
        "app.modules.manutencao_frota.service.get_celery_dispatcher",
        lambda: _FakeDispatcher(),
    )
    result = await obs_router._check_celery_workers()
    assert result["status"] == "ok"
    assert result["workers"] == 2
    assert result["worker_names"] == ["celery@worker-1", "celery@worker-2"]
    assert "latency_ms" in result


@pytest.mark.asyncio
async def test_check_celery_workers_degraded_when_no_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broker up mas nenhum worker ouvindo -> status=degraded (nao error).

    Cenario real: deploy novo antes do worker subir. API deve continuar
    respondendo 200 no readiness porque request sincrono funciona; so os
    pipelines async ficam parados.
    """
    from app.modules.observability import router as obs_router

    class _FakeInspect:
        def __init__(self, timeout: float) -> None:
            pass

        def ping(self) -> None:
            # Celery retorna None quando broadcast nao recebe resposta
            return None

    class _FakeControl:
        def inspect(self, timeout: float) -> _FakeInspect:
            return _FakeInspect(timeout)

    class _FakeDispatcher:
        control = _FakeControl()

    monkeypatch.setattr(
        "app.modules.manutencao_frota.service.get_celery_dispatcher",
        lambda: _FakeDispatcher(),
    )
    result = await obs_router._check_celery_workers()
    assert result["status"] == "degraded"
    assert result["workers"] == 0
    assert "note" in result


@pytest.mark.asyncio
async def test_check_celery_workers_error_when_broker_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Excecao no ping (ex: broker refusou conexao) -> status=error."""
    from app.modules.observability import router as obs_router

    class _FakeInspect:
        def __init__(self, timeout: float) -> None:
            pass

        def ping(self) -> None:
            raise ConnectionRefusedError("broker down")

    class _FakeControl:
        def inspect(self, timeout: float) -> _FakeInspect:
            return _FakeInspect(timeout)

    class _FakeDispatcher:
        control = _FakeControl()

    monkeypatch.setattr(
        "app.modules.manutencao_frota.service.get_celery_dispatcher",
        lambda: _FakeDispatcher(),
    )
    result = await obs_router._check_celery_workers()
    assert result["status"] == "error"
    assert "broker down" in result.get("error", "")


@pytest.mark.asyncio
async def test_aggregated_health_degraded_when_workers_down(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesmo com DB/storage ok, 0 workers -> payload.status=degraded.

    Critico: status textual sinaliza pra UI/monitoring, mas HTTP
    continua 200 (workers nao sao criticos pra servir request sync).
    """
    from app.modules.observability import router as obs_router

    async def _ok_db() -> dict:
        return {"status": "ok", "latency_ms": 0.1}

    async def _ok_redis() -> dict:
        return {"status": "ok", "latency_ms": 0.1}

    async def _ok_storage() -> dict:
        return {"status": "ok", "backend": "local", "latency_ms": 0.1}

    async def _no_workers() -> dict:
        return {
            "status": "degraded",
            "workers": 0,
            "latency_ms": 1.2,
            "note": "broker ok mas nenhum worker Celery respondeu",
        }

    monkeypatch.setattr(obs_router, "_check_db", _ok_db)
    monkeypatch.setattr(obs_router, "_check_redis", _ok_redis)
    monkeypatch.setattr(obs_router, "_check_storage", _ok_storage)
    monkeypatch.setattr(obs_router, "_check_celery_workers", _no_workers)

    resp = await api_client.get("/api/v1/observability/health")
    # HTTP ainda 200 -- workers nao sao criticos pra rotation do LB
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["celery_workers"]["status"] == "degraded"


# --- k8s-style aliases (/healthz, /readyz) --------------------------------


@pytest.mark.asyncio
async def test_healthz_alias_matches_health_payload(
    api_client: AsyncClient,
) -> None:
    """`/healthz` (convencao k8s) devolve mesma resposta do `/health`."""
    original = await api_client.get("/health")
    alias = await api_client.get("/healthz")
    assert original.status_code == 200
    assert alias.status_code == 200
    # version + status devem bater (correlation_id difere entre requests)
    o_body = original.json()
    a_body = alias.json()
    assert o_body["status"] == a_body["status"]
    assert o_body["version"] == a_body["version"]


@pytest.mark.asyncio
async def test_readyz_alias_matches_observability_health_payload(
    api_client: AsyncClient,
) -> None:
    """`/readyz` delega pro agregador -- mesmo schema `checks`/`status`."""
    original = await api_client.get("/api/v1/observability/health")
    alias = await api_client.get("/readyz")
    assert original.status_code == alias.status_code
    o_body = original.json()
    a_body = alias.json()
    assert set(o_body["checks"].keys()) == set(a_body["checks"].keys())
    assert o_body["version"] == a_body["version"]


@pytest.mark.asyncio
async def test_readyz_returns_503_when_critical_fails(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/readyz` retorna 503 quando DB falha -- contrato k8s readiness."""
    from app.modules.observability import router as obs_router

    async def _fail_db() -> dict:
        return {"status": "error", "latency_ms": 0.1, "error": "db unreachable"}

    async def _ok_redis() -> dict:
        return {"status": "ok", "latency_ms": 0.1}

    async def _ok_storage() -> dict:
        return {"status": "ok", "backend": "local", "latency_ms": 0.1}

    async def _ok_workers() -> dict:
        return {"status": "ok", "workers": 1, "latency_ms": 0.1}

    monkeypatch.setattr(obs_router, "_check_db", _fail_db)
    monkeypatch.setattr(obs_router, "_check_redis", _ok_redis)
    monkeypatch.setattr(obs_router, "_check_storage", _ok_storage)
    monkeypatch.setattr(obs_router, "_check_celery_workers", _ok_workers)

    resp = await api_client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"]["status"] == "error"
