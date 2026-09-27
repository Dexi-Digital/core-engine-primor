from __future__ import annotations

import asyncio
from datetime import date

import pytest

from worker.tasks import licitacoes


def test_backfill_pncp_processa_janelas_semanais(monkeypatch) -> None:
    chamadas: list[tuple[str, str, str | None]] = []

    async def fake_run(inicio, fim, uf, max_paginas):
        chamadas.append((inicio, fim, uf))
        assert max_paginas is None
        return {"total_fetched": 3, "inserted": 2}

    monkeypatch.setattr(licitacoes, "_run", fake_run)
    result = asyncio.run(
        licitacoes._run_backfill(date(2026, 4, 1), date(2026, 4, 15), "MG")
    )

    assert chamadas == [
        ("2026-04-01", "2026-04-07", "MG"),
        ("2026-04-08", "2026-04-14", "MG"),
        ("2026-04-15", "2026-04-15", "MG"),
    ]
    assert result["total_fetched"] == 9
    assert result["inserted"] == 6
    assert result["failed_windows"] == []


def test_backfill_pncp_isola_falha_por_janela(monkeypatch) -> None:
    async def fake_run(inicio, fim, uf, max_paginas):
        if inicio == "2026-04-01":
            raise RuntimeError("PNCP indisponivel")
        return {"total_fetched": 2, "inserted": 2}

    monkeypatch.setattr(licitacoes, "_run", fake_run)
    result = asyncio.run(
        licitacoes._run_backfill(date(2026, 4, 1), date(2026, 4, 14), None)
    )

    assert result["total_fetched"] == 2
    assert result["failed_windows"] == [{
        "data_inicial": "2026-04-01",
        "data_final": "2026-04-07",
        "erro": "RuntimeError: PNCP indisponivel",
    }]


def test_backfill_pncp_limita_execucao_a_um_ano() -> None:
    with pytest.raises(ValueError, match="367 dias"):
        licitacoes.backfill_pncp("2025-01-01", "2026-01-04")
