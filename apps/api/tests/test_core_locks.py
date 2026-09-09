"""Lock single-flight no Redis.

Decisao #2 do TOTVS: sessao do RM consome licenca de acesso concorrente
-- concorrencia e dinheiro e risco de travar o ERP. Duas execucoes do
mesmo pull nunca podem se sobrepor.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.locks import single_flight


class FakeRedis:
    """Redis em memoria com o subset usado pelo lock (SET NX EX + EVAL)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, str, int]] = []

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.sets.append((key, value, ex))
        return True

    async def eval(self, script, numkeys, key, arg):
        # compare-and-delete
        if self.store.get(key) == arg:
            del self.store[key]
            return 1
        return 0

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_adquire_quando_livre():
    r = FakeRedis()
    async with single_flight("totvs:pull", redis=r, ttl_s=60) as adquiriu:
        assert adquiriu is True


@pytest.mark.asyncio
async def test_segunda_execucao_concorrente_nao_adquire():
    r = FakeRedis()
    async with single_flight("totvs:pull", redis=r, ttl_s=60) as primeiro:
        assert primeiro is True
        async with single_flight("totvs:pull", redis=r, ttl_s=60) as segundo:
            assert segundo is False


@pytest.mark.asyncio
async def test_lock_e_liberado_na_saida():
    r = FakeRedis()
    async with single_flight("totvs:pull", redis=r, ttl_s=60):
        pass
    async with single_flight("totvs:pull", redis=r, ttl_s=60) as adquiriu:
        assert adquiriu is True


@pytest.mark.asyncio
async def test_lock_e_liberado_mesmo_com_excecao():
    """Pull que estoura nao pode deixar o lock preso ate o TTL --
    senao a execucao da noite seguinte tambem e pulada."""
    r = FakeRedis()
    with pytest.raises(RuntimeError):
        async with single_flight("totvs:pull", redis=r, ttl_s=60):
            raise RuntimeError("pull falhou")
    assert r.store == {}


@pytest.mark.asyncio
async def test_ttl_e_aplicado_no_set():
    """Sem TTL, um worker morto (OOM/deploy) prende o lock para sempre."""
    r = FakeRedis()
    async with single_flight("totvs:pull", redis=r, ttl_s=3600):
        pass
    assert r.sets[0][2] == 3600


@pytest.mark.asyncio
async def test_nao_libera_lock_de_outro_dono():
    """Se o TTL estourou e outro worker pegou o lock, a nossa saida NAO
    pode deletar o lock dele."""
    r = FakeRedis()
    async with single_flight("totvs:pull", redis=r, ttl_s=60) as adquiriu:
        assert adquiriu
        # simula expiry + outro worker assumindo
        r.store["totvs:pull"] = "token-de-outro-worker"
    assert r.store["totvs:pull"] == "token-de-outro-worker"


@pytest.mark.asyncio
async def test_execucoes_realmente_concorrentes_se_excluem():
    r = FakeRedis()
    resultados: list[bool] = []

    async def tentar():
        async with single_flight("totvs:pull", redis=r, ttl_s=60) as ok:
            resultados.append(ok)
            await asyncio.sleep(0.01)

    await asyncio.gather(tentar(), tentar(), tentar())
    assert sorted(resultados) == [False, False, True]
