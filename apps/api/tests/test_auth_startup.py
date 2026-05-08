"""Tests para warn_dev_secret + guard_local_storage_in_prod."""
from __future__ import annotations

import logging

import pytest
import structlog

from app.core.config import Settings
from app.modules.auth.startup import (
    InsecureProductionSecretError,
    InsecureProductionStorageError,
    guard_local_storage_in_prod,
    warn_dev_secret,
)


def _settings(**overrides) -> Settings:
    base = {"secret_key": "change-me-in-production", "environment": "development"}
    base.update(overrides)
    return Settings(**base)


def test_dev_default_in_dev_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    # structlog nao propaga pra `caplog` por default; configuramos o
    # LoggerFactory para stdlib antes da chamada, depois revertemos.
    # Isso garante que o teste reflita o que o operador ve em dev.
    prev = structlog.get_config()
    structlog.configure(
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    try:
        with caplog.at_level(logging.WARNING, logger="app.modules.auth.startup"):
            warn_dev_secret(_settings(environment="development"))
    finally:
        structlog.configure(**prev)
    assert any(
        "dev default" in rec.getMessage() or "forgeable" in rec.getMessage()
        for rec in caplog.records
    ), f"warning nao emitido; records={[r.getMessage() for r in caplog.records]}"


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


# --- guard_local_storage_in_prod -------------------------------------------


def test_storage_guard_dev_is_noop() -> None:
    # Em dev o guard nao faz nada mesmo com /tmp -- caso contrario
    # desenvolvedor com `/tmp/motor-central` default nao sobe app.
    guard_local_storage_in_prod(
        _settings(
            environment="development",
            storage_backend="local",
            editais_storage_path="/tmp/motor-central/editais",
        )
    )


def test_storage_guard_prod_local_tmp_raises() -> None:
    with pytest.raises(InsecureProductionStorageError):
        guard_local_storage_in_prod(
            _settings(
                environment="production",
                secret_key="x" * 64,
                storage_backend="local",
                editais_storage_path="/tmp/motor-central/editais",
            )
        )


def test_storage_guard_prod_local_var_tmp_raises() -> None:
    with pytest.raises(InsecureProductionStorageError):
        guard_local_storage_in_prod(
            _settings(
                environment="production",
                secret_key="x" * 64,
                storage_backend="local",
                editais_storage_path="/var/tmp/foo",
            )
        )


def test_storage_guard_prod_local_persistent_path_passes() -> None:
    # Path em volume montado -> OK.
    guard_local_storage_in_prod(
        _settings(
            environment="production",
            secret_key="x" * 64,
            storage_backend="local",
            editais_storage_path="/data/motor-central/editais",
        )
    )


def test_storage_guard_prod_onedrive_tmp_is_noop() -> None:
    # OneDrive ignora o path local -- guard nao deve reclamar.
    guard_local_storage_in_prod(
        _settings(
            environment="production",
            secret_key="x" * 64,
            storage_backend="onedrive",
            editais_storage_path="/tmp/whatever",
        )
    )


def test_storage_guard_prod_alias_raises() -> None:
    # Mesma politica de caixa/alias que o secret guard.
    for env in ("production", "PRODUCTION", "Prod", "prod"):
        with pytest.raises(InsecureProductionStorageError):
            guard_local_storage_in_prod(
                _settings(
                    environment=env,
                    secret_key="x" * 64,
                    storage_backend="local",
                    editais_storage_path="/tmp/x",
                )
            )
