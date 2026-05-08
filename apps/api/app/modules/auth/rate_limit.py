"""Rate limiter simples para o login.

Motivacao: o `/api/v1/auth/login` e o endpoint mais exposto da API e o
unico sem exigencia de token. Sem rate limit, um atacante pode rodar
brute force em qualquer email conhecido (ex.: `sistemas@primor...`)
sem custo nenhum. Com 5 tentativas/minuto por (email, ip), o melhor
caminho e resetar senha ou pedir ao admin -- bruteforce vira ruido.

Implementacao:
- Redis como backend (mesmo server ja usado por Celery). Um `INCR`
  com `EXPIRE` na primeira ocorrencia -- fixed window de `window_s`
  segundos. Nao e sliding window, mas e "bom o suficiente" (o worst
  case e 2x tentativas exatas no overlap entre janelas, o que ainda
  esta longe de viabilizar brute force).
- **Fail-open**: se o Redis estiver indisponivel (broker down, DNS
  etc), o limiter *permite* a tentativa e loga warning. A alternativa
  (fail-closed) deixaria a API totalmente inacessivel se o Redis
  cair -- pior que temporariamente desabilitar o rate limit.
- Chave: `auth:login:{email_lower}:{ip}` -- normalizamos o email
  (case/strip) para nao poder contornar com alternancia de caixa.
- O limiter NAO diferencia tentativa bem-sucedida vs falha: ambos
  contam para o bucket. Essa e a escolha conservadora (um atacante
  poderia spray de emails validos para pescar mfa/recuperacao em
  paralelo); clientes honestos que acertam a senha raramente batem
  no limite.
- Key esta normalizada para minimizar bypass: email case-insensitive,
  IP via `X-Forwarded-For` quando disponivel + `request.client.host`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuracao do limiter (lida do `Settings`).

    `enabled` False sobe o limiter em no-op -- usado nos testes que nao
    exercitam o rate limit em si mas passam pelo endpoint `/login`.
    """
    enabled: bool
    max_attempts: int
    window_s: int


def _config_from_settings(settings: Settings) -> RateLimitConfig:
    return RateLimitConfig(
        enabled=settings.login_rate_limit_enabled,
        max_attempts=settings.login_rate_limit_max_attempts,
        window_s=settings.login_rate_limit_window_s,
    )


def _client_ip(request: Request) -> str:
    """Extrai o IP do client. Respeita `X-Forwarded-For` quando presente
    (deploys com reverse proxy usam esse header) e cai para
    `request.client.host` caso contrario. Pega so o primeiro hop do XFF
    -- os subsequentes sao do proxy chain, nao do client real.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


def _key(email: str, ip: str) -> str:
    norm_email = (email or "").strip().lower()
    return f"auth:login:{norm_email}:{ip}"


async def _incr_with_ttl(redis_url: str, key: str, window_s: int) -> int:
    """Incrementa o contador do bucket e aplica TTL na primeira vez.

    Retorna o valor atual do contador apos o incremento. Erros do Redis
    sao propagados para o caller -- que trata como fail-open.
    """
    from redis.asyncio import Redis

    redis = Redis.from_url(redis_url)
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s, nx=True)
            result = await pipe.execute()
        # `INCR` retorna int; `EXPIRE NX` retorna bool.
        return int(result[0])
    finally:
        await redis.aclose()


async def enforce_login_rate_limit(
    request: Request,
    email: str,
    *,
    settings: Settings | None = None,
) -> None:
    """Dispara `HTTPException(429)` se a combinacao (email, ip) excedeu o
    limite. Em caso de erro no Redis, **fail-open** -- so loga warning.

    Chamado do router **antes** de verificar credenciais. Isso evita
    que o cost da autenticacao (bcrypt) vire um oracle para quem quer
    enumerar emails via timing.
    """
    settings = settings or get_settings()
    config = _config_from_settings(settings)
    if not config.enabled:
        return

    ip = _client_ip(request)
    key = _key(email, ip)
    try:
        count = await _incr_with_ttl(
            settings.redis_url, key, config.window_s
        )
    except Exception as exc:  # noqa: BLE001
        # Fail-open: se o Redis caiu, nao vamos trancar todo mundo.
        # O cron de observability ja detecta o broker down em /readyz.
        logger.warning(
            "rate limit falhou (fail-open): %s", exc, extra={"key": key}
        )
        return

    if count > config.max_attempts:
        logger.info(
            "login rate limit atingido",
            extra={"email": email, "ip": ip, "count": count},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Muitas tentativas de login. "
                f"Tente novamente em ate {config.window_s}s."
            ),
            headers={"Retry-After": str(config.window_s)},
        )


__all__ = [
    "RateLimitConfig",
    "enforce_login_rate_limit",
]
