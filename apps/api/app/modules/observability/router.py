"""Endpoints de observability.

`GET /api/v1/observability/health` agrega checks de DB + Redis +
storage. Diferente de `GET /health` (liveness simples no `main.py`),
este endpoint e *readiness*: so retorna 200 quando todas as
dependencias criticas estao realmente respondendo. Util para
load-balancer/k8s decidir se a instancia pode receber trafego.

Cada check tem timeout curto (~2s) para nao deixar uma dep travada
manter o endpoint pendurado -- e melhor responder rapido com `degraded`
do que segurar o cliente.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal

logger = structlog.get_logger(__name__)

router = APIRouter()

CHECK_TIMEOUT_SECONDS = 2.0


async def _check_db() -> dict[str, Any]:
    """SELECT 1 contra o DB -- mede latencia do round-trip."""
    started = time.perf_counter()
    try:
        async with SessionLocal() as db:  # type: AsyncSession
            await asyncio.wait_for(
                db.execute(text("SELECT 1")), timeout=CHECK_TIMEOUT_SECONDS
            )
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except TimeoutError:
        return {
            "status": "timeout",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": f"db check exceeded {CHECK_TIMEOUT_SECONDS}s",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc)[:256],
        }


async def _check_redis() -> dict[str, Any]:
    """Roda `PING` contra o broker Celery (Redis).

    Usa `redis.asyncio` em vez de mexer com `Celery.control.ping()` -- esta
    ultima dispara um broadcast que depende dos workers estarem rodando.
    O readiness do API quer saber se o BROKER esta de pe, nao o worker.
    """
    started = time.perf_counter()
    settings = get_settings()
    try:
        from redis.asyncio import Redis
    except ImportError:
        return {
            "status": "skipped",
            "error": "redis.asyncio not installed",
        }
    redis = Redis.from_url(settings.redis_url)
    try:
        await asyncio.wait_for(redis.ping(), timeout=CHECK_TIMEOUT_SECONDS)
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except TimeoutError:
        return {
            "status": "timeout",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": f"redis ping exceeded {CHECK_TIMEOUT_SECONDS}s",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc)[:256],
        }
    finally:
        await redis.aclose()


async def _check_storage() -> dict[str, Any]:
    """Verifica acesso ao backend de storage configurado."""
    settings = get_settings()
    backend = (getattr(settings, "storage_backend", None) or "local").lower()
    started = time.perf_counter()
    if backend == "onedrive":
        # Em onedrive, batemos health_check do OneDriveClient se as
        # credenciais estao todas configuradas; senao consideramos
        # o backend "skipped" (vai cair em mock no codigo de upload).
        try:
            from app.integrations.onedrive.client import build_onedrive_client
        except ImportError as exc:  # pragma: no cover
            return {
                "status": "error",
                "backend": backend,
                "error": f"onedrive module missing: {exc}",
            }
        if not (
            getattr(settings, "ms_graph_tenant_id", None)
            and getattr(settings, "ms_graph_client_id", None)
            and getattr(settings, "ms_graph_client_secret", None)
            and getattr(settings, "ms_graph_drive_id", None)
        ):
            return {
                "status": "mock",
                "backend": backend,
                "note": "credenciais MS_GRAPH_* incompletas",
            }
        client = build_onedrive_client(
            tenant_id=settings.ms_graph_tenant_id,
            client_id=settings.ms_graph_client_id,
            client_secret=settings.ms_graph_client_secret,
            drive_id=settings.ms_graph_drive_id,
            root_folder=getattr(
                settings, "ms_graph_root_folder", "/MotorCentral"
            ),
        )
        try:
            ok = await asyncio.wait_for(
                client.health_check(), timeout=CHECK_TIMEOUT_SECONDS
            )
            return {
                "status": "ok" if ok else "error",
                "backend": backend,
                "latency_ms": round(
                    (time.perf_counter() - started) * 1000, 2
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "backend": backend,
                "error": str(exc)[:256],
            }
        finally:
            await client.aclose()

    # local: confere que o diretorio existe (e cria se faltar).
    base = Path(settings.editais_storage_path)
    try:
        base.mkdir(parents=True, exist_ok=True)
        # Tenta tocar um arquivo sentinela para garantir write access.
        sentinel = base / ".healthcheck"
        sentinel.write_text("ok", encoding="utf-8")
        sentinel.unlink(missing_ok=True)
        return {
            "status": "ok",
            "backend": backend,
            "path": str(base),
            "latency_ms": round(
                (time.perf_counter() - started) * 1000, 2
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "backend": backend,
            "path": str(base),
            "error": str(exc)[:256],
        }


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Readiness check agregado.

    Devolve 200 se todos os checks responderam (mesmo com `mock`/`skipped`),
    503 se qualquer check critico (DB ou Redis) deu `error`/`timeout`.
    Storage `error` tambem retorna 503 -- sem storage o pipeline de
    OCR/edital nao funciona.
    """
    settings = get_settings()
    db_check, redis_check, storage_check = await asyncio.gather(
        _check_db(), _check_redis(), _check_storage()
    )
    checks = {
        "db": db_check,
        "redis": redis_check,
        "storage": storage_check,
    }
    critical_failed = any(
        c.get("status") in {"error", "timeout"} for c in checks.values()
    )
    payload = {
        "status": "degraded" if critical_failed else "ok",
        "version": settings.app_version,
        "correlation_id": getattr(request.state, "correlation_id", None),
        "checks": checks,
    }
    return JSONResponse(payload, status_code=503 if critical_failed else 200)


__all__ = ["router"]
