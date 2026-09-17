"""Comparacao de versao entre API e worker.

Existe por causa de um incidente real: a imagem do worker carrega o
pacote da API dentro dela, entao alteracao em `apps/api` exige redeploy
dos DOIS servicos. Em 16/09/2026 o PR #67 mudou o gatilho de manutencao
preventiva de 250h para 50h, a API subiu e o worker ficou para tras --
os dois passaram a rodar regras diferentes, sem erro nenhum.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.version import code_fingerprint
from app.modules.observability.versao import (
    comparar_versoes,
    registrar_boot_worker,
)


def test_fingerprint_e_estavel_e_curto():
    f1 = code_fingerprint()
    code_fingerprint.cache_clear()
    assert f1 == code_fingerprint(), "mesmo codigo, mesmo hash"
    assert len(f1) == 12 and all(c in "0123456789abcdef" for c in f1)


@pytest.mark.asyncio
async def test_sem_boot_do_worker_nao_afirma_sincronia(
    db_session: AsyncSession,
):
    """Worker que nunca subiu NAO esta "em dia" -- e desconhecido.
    Afirmar sincronia aqui seria pior do que nao afirmar nada."""
    r = await comparar_versoes(db_session)
    assert r["worker_fingerprint"] is None
    assert r["em_sincronia"] is None
    assert r["api_fingerprint"] == code_fingerprint()


@pytest.mark.asyncio
async def test_worker_na_mesma_versao_aparece_em_sincronia(
    db_session: AsyncSession,
):
    await registrar_boot_worker(db_session, fingerprint=code_fingerprint())
    r = await comparar_versoes(db_session)
    assert r["em_sincronia"] is True
    assert r["worker_boot_em"] is not None


@pytest.mark.asyncio
async def test_worker_defasado_e_detectado(db_session: AsyncSession):
    """O caso do incidente: worker com codigo antigo."""
    await registrar_boot_worker(db_session, fingerprint="000000000000")
    r = await comparar_versoes(db_session)
    assert r["em_sincronia"] is False
    assert r["worker_fingerprint"] == "000000000000"
    assert r["api_fingerprint"] != r["worker_fingerprint"]


@pytest.mark.asyncio
async def test_usa_o_boot_mais_recente(db_session: AsyncSession):
    """Redeploy corrige a defasagem -- o ultimo boot e o que vale."""
    await registrar_boot_worker(db_session, fingerprint="000000000000")
    await registrar_boot_worker(db_session, fingerprint=code_fingerprint())
    r = await comparar_versoes(db_session)
    assert r["em_sincronia"] is True


@pytest.mark.asyncio
async def test_metadata_corrompido_nao_quebra(db_session: AsyncSession):
    """Telemetria ruim nao pode derrubar a consulta de diagnostico."""
    from app.audit.models import AuditLog
    from app.modules.observability.versao import (
        ACAO_BOOT_WORKER,
        RECURSO_VERSAO,
    )

    db_session.add(
        AuditLog(
            actor="system:worker",
            action=ACAO_BOOT_WORKER,
            resource=RECURSO_VERSAO,
            metadata_json="{nao e json",
        )
    )
    await db_session.commit()
    r = await comparar_versoes(db_session)
    assert r["worker_fingerprint"] is None
    assert r["em_sincronia"] is None


@pytest.mark.asyncio
async def test_endpoint_versao(api_client, auth_headers):
    resp = await api_client.get(
        "/api/v1/observability/versao", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_fingerprint"] == code_fingerprint()
    assert "em_sincronia" in body
    json.dumps(body)  # serializavel
