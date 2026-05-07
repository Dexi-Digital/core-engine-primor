"""Tests para warn_dev_secret -- garante secret guard em prod."""
from __future__ import annotations

import logging

import pytest

from app.core.config import Settings
from app.modules.auth.startup import (
    InsecureProductionSecretError,
    warn_dev_secret,
)


def _settings(**overrides) -> Settings:
    base = {"secret_key": "change-me-in-production", "environment": "development"}
    base.update(overrides)
    return Settings(**base)


def test_dev_default_in_dev_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warn_dev_secret(_settings(environment="development"))
    # Nao raise; apenas loga (structlog vai pra stdlib em testes).


def test_dev_default_in_production_raises() -> None:
    with pytest.raises(InsecureProductionSecretError):
        warn_dev_secret(_settings(environment="production"))


def test_dev_default_in_prod_alias_raises() -> None:
    # Aceita tanto "production" quanto "prod" e variacoes de caixa.
    for env in ("production", "PRODUCTION", "Prod", "prod"):
        with pytest.raises(InsecureProductionSecretError):
            warn_dev_secret(_settings(environment=env))


def test_custom_secret_in_production_passes() -> None:
    # Sem raise: a chave foi customizada (qualquer coisa != default).
    warn_dev_secret(
        _settings(environment="production", secret_key="x" * 64)
    )


def test_custom_secret_in_dev_passes() -> None:
    warn_dev_secret(
        _settings(environment="development", secret_key="x" * 64)
    )
