"""Modulo juridico -- contencioso trabalhista vindo do EasyJur.

O client e substituido por um duble que devolve as fixtures
anonimizadas. Nenhum teste toca o EasyJur real: cada login errado la
aproxima o bloqueio da conta de quem usa o sistema para trabalhar.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.juridico import service as svc
from app.modules.juridico.models import Andamento, Processo, SyncLog

pytestmark = pytest.mark.asyncio
FIX = Path(__file__).parent / "fixtures" / "easyjur"


class ClientFalso:
    """Devolve a fixture na pagina 1 e uma pagina vazia depois."""

    def __init__(self, *, html: str | None = None, csv: bytes | None = None):
        self.html = html or (FIX / "processos_pagina.html").read_text()
        self.csv = csv or (FIX / "andamentos_export.csv").read_bytes()
        self.paginas_pedidas: list[int] = []

    async def listar_processos(self, page: int = 1) -> str:
        self.paginas_pedidas.append(page)
        if page == 1:
            return self.html
        return '<table><tbody></tbody></table><h3>453  Registros Encontrados</h3>'

    async def exportar_andamentos_csv(self) -> bytes:
        return self.csv

    async def aclose(self) -> None:
        pass


async def _contar(db: AsyncSession, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_sincroniza_processos_e_andamentos(db_session: AsyncSession) -> None:
    r = await svc.sincronizar(db_session, ClientFalso(), source="manual")
    assert r.processos == 3
    assert r.andamentos == 3
    assert await _contar(db_session, Processo) == 3
    assert await _contar(db_session, Andamento) == 3


async def test_rodar_duas_vezes_nao_duplica(db_session: AsyncSession) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    assert await _contar(db_session, Processo) == 3
    assert await _contar(db_session, Andamento) == 3


async def test_andamento_liga_ao_processo_pelo_cnj(db_session: AsyncSession) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    ligados = (
        await db_session.execute(
            select(Andamento).where(Andamento.processo_id.is_not(None))
        )
    ).scalars().all()
    assert len(ligados) == 2  # os dois do CNJ ...0001


async def test_andamento_de_processo_desconhecido_entra_sem_vinculo(
    db_session: AsyncSession,
) -> None:
    """O feed cita processos por numero, e nem todo numero citado esta
    entre os processos listados. Descartar o andamento perderia
    movimentacao real; inventar o processo seria pior."""
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    orfao = (
        await db_session.execute(
            select(Andamento).where(Andamento.numero_cnj.like("0000009%"))
        )
    ).scalar_one()
    assert orfao.processo_id is None


async def test_divergencia_com_o_total_declarado_e_registrada(
    db_session: AsyncSession,
) -> None:
    """A fixture declara 453 e traz 3. O pull NAO pode aceitar isso em
    silencio -- foi uma resposta "vazia com cara de certa" que fez 453
    processos virarem 0 em 17/09/2026."""
    r = await svc.sincronizar(db_session, ClientFalso(), source="manual")
    assert r.total_declarado == 453
    assert r.divergencia is True
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.divergencia is True


async def test_manual_nao_queima_a_janela_do_beat(db_session: AsyncSession) -> None:
    """Mesmo precedente do ponto e do TOTVS: `source` faz parte da
    chave do log. Sem isso, um "sincronizar agora" as 10h faria o job
    agendado do dia achar que ja rodou."""
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    await svc.sincronizar(db_session, ClientFalso(), source="beat")
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    logs = (await db_session.execute(select(SyncLog))).scalars().all()
    assert sorted(x.source for x in logs) == ["beat", "manual"]


async def test_obra_nao_cadastrada_guarda_o_codigo_sem_inventar_obra(
    db_session: AsyncSession,
) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    p = (
        await db_session.execute(select(Processo).where(Processo.codigo_obra == "231"))
    ).scalar_one()
    assert p.obra_id is None


async def test_resumo_devolve_o_denominador_dos_campos_esparsos(
    db_session: AsyncSession,
) -> None:
    """ "Risco remoto em 100% dos processos" seria falso: o verdadeiro
    e "2 de 2 preenchidos, num universo de 3". Agregar campo esparso
    sem o denominador transforma cobertura baixa em afirmacao sobre a
    carteira inteira."""
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    resumo = await svc.resumo(db_session)
    assert resumo["total"] == 3
    risco = resumo["campos_esparsos"]["risco"]
    assert risco["preenchidos"] == 2
    assert risco["total"] == 3


async def test_api_lista_processos(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    r = await api_client.get("/api/v1/juridico/processos", headers=auth_headers)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["total"] == 3
    assert corpo["data"][0]["numero_cnj"]


async def test_api_exige_autenticacao(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/juridico/processos")).status_code == 401


# --- "Sincronizar agora" roda no worker, e a tela ve o estado -----------------
#
# A primeira versao bloqueava a requisicao por ~2,5 min sem feedback, e a
# server action ENGOLIA o 502/503: clicar no botao e nada mudar na tela era
# indistinguivel de "rodou". Relatado em 21/09/2026.


class _DispatcherFalso:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, list, str | None]] = []

    def send_task(self, name: str, args: list | None = None, queue: str | None = None, **kw):
        self.chamadas.append((name, args or [], queue))


@pytest.fixture
def com_credencial(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("EASYJUR_EMAIL", "x@y.com")
    monkeypatch.setenv("EASYJUR_PASSWORD", "s")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_sync_sem_credencial_da_503_e_fica_registrado(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O motivo tem de aparecer na tela, nao so no status HTTP que a
    server action pode engolir."""
    from app.core.config import get_settings

    # setenv("") e nao delenv: o .env local da maquina de dev tem a
    # credencial, e pydantic-settings le o arquivo quando a variavel
    # nao existe no ambiente.
    monkeypatch.setenv("EASYJUR_EMAIL", "")
    monkeypatch.setenv("EASYJUR_PASSWORD", "")
    get_settings.cache_clear()
    r = await api_client.post("/api/v1/juridico/sync", headers=auth_headers)
    get_settings.cache_clear()
    assert r.status_code == 503
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.status == "erro"
    assert "EASYJUR" in (log.erro or "")


async def test_sync_enfileira_no_worker_e_devolve_202(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, com_credencial,
) -> None:
    disp = _DispatcherFalso()
    monkeypatch.setattr(svc, "get_celery_dispatcher", lambda: disp)

    r = await api_client.post("/api/v1/juridico/sync", headers=auth_headers)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "em_andamento"
    assert disp.chamadas == [("worker.tasks.juridico.pull_easyjur", ["manual"], "financeiro")]
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.status == "em_andamento"
    assert log.iniciado_em is not None


async def test_sync_com_fila_fora_do_ar_da_503_e_registra(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, com_credencial,
) -> None:
    class _Quebrado:
        def send_task(self, *a, **k):
            raise ConnectionError("redis recusou")

    monkeypatch.setattr(svc, "get_celery_dispatcher", lambda: _Quebrado())
    r = await api_client.post("/api/v1/juridico/sync", headers=auth_headers)
    assert r.status_code == 503
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.status == "erro"
    assert "fila" in (log.erro or "").lower()


async def test_sync_em_andamento_nao_enfileira_de_novo(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, com_credencial,
) -> None:
    """Dois cliques = uma carga. Duas cargas simultaneas fariam dois
    logins e dois exports de 12 mil linhas contra o EasyJur."""
    disp = _DispatcherFalso()
    monkeypatch.setattr(svc, "get_celery_dispatcher", lambda: disp)
    assert (await api_client.post("/api/v1/juridico/sync", headers=auth_headers)).status_code == 202
    r = await api_client.post("/api/v1/juridico/sync", headers=auth_headers)
    assert r.status_code == 409
    assert len(disp.chamadas) == 1


async def test_sync_travado_ha_muito_tempo_pode_ser_reenfileirado(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se o worker nao existir no ambiente, a carga fica "em andamento"
    para sempre. Depois de 30 min o botao volta a funcionar -- e a tela
    avisa que o worker pode estar fora."""
    from datetime import UTC, datetime, timedelta

    await svc.marcar_inicio(db_session, source="manual")
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    log.iniciado_em = datetime.now(UTC) - timedelta(minutes=31)
    await db_session.commit()
    assert await svc.em_andamento(db_session, source="manual") is False


async def test_erro_no_pull_fica_no_log(db_session: AsyncSession) -> None:
    await svc.marcar_inicio(db_session, source="beat")
    await svc.marcar_erro(db_session, source="beat", mensagem="EasyJur recusou o login")
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.status == "erro"
    assert log.erro == "EasyJur recusou o login"


async def test_pull_bem_sucedido_fecha_como_ok(db_session: AsyncSession) -> None:
    await svc.marcar_inicio(db_session, source="manual")
    await svc.sincronizar(db_session, ClientFalso(), source="manual")
    log = (await db_session.execute(select(SyncLog))).scalar_one()
    assert log.status == "ok"
    assert log.erro is None


async def test_resumo_expoe_o_estado_do_sync(db_session: AsyncSession) -> None:
    await svc.marcar_inicio(db_session, source="manual")
    u = (await svc.resumo(db_session))["ultimo_sync"]
    assert u["status"] == "em_andamento"
    assert u["iniciado_em"] is not None
