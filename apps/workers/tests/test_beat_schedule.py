"""O beat so pode agendar task que existe -- e a esteira precisa ingerir.

Dois erros reais motivaram este arquivo, os dois silenciosos:

1. **Agendar nome que nao existe.** Celery aceita a entrada no
   `beat_schedule` sem reclamar e so falha na hora de executar, com um
   "Received unregistered task" perdido no log do worker. O agendamento
   parece feito e nada roda.

2. **Boletim sem ingestao.** Ate 21/09/2026 o beat despachava boletim
   3x/dia enquanto o `crawler_pncp` -- que existia -- nunca tinha sido
   agendado. A base nao era atualizada por ninguem e a oportunidade mais
   recente da tela ficou parada por meses, sem erro em lugar nenhum.
"""
from __future__ import annotations

import pytest

from worker.main import celery_app


@pytest.fixture(scope="module")
def tasks_registradas() -> set[str]:
    # Importa todos os modulos de task: sem isso o registro do Celery
    # fica vazio e o teste passaria por engano.
    import worker.tasks.dp_sesmt  # noqa: F401
    import worker.tasks.financeiro  # noqa: F401
    import worker.tasks.fiscal  # noqa: F401
    import worker.tasks.ia  # noqa: F401
    import worker.tasks.licitacoes  # noqa: F401
    import worker.tasks.manutencao  # noqa: F401
    import worker.tasks.onedrive_diagnostico  # noqa: F401

    return set(celery_app.tasks.keys())


def test_toda_entrada_do_beat_aponta_para_task_existente(
    tasks_registradas: set[str],
) -> None:
    agendadas = {
        nome: cfg["task"]
        for nome, cfg in celery_app.conf.beat_schedule.items()
    }
    orfas = {
        nome: task
        for nome, task in agendadas.items()
        if task not in tasks_registradas
    }
    assert not orfas, (
        f"entradas do beat sem task registrada: {orfas}. "
        "Celery aceita isso e so falha em runtime."
    )


def test_esteira_de_licitacoes_tem_ingestao_agendada() -> None:
    """Boletim sem crawler e notificacao sobre dado morto.

    Se alguem remover o `crawler_pncp` do beat mantendo os boletins, o
    sistema volta a avisar 3x/dia sobre uma base congelada -- que foi
    exatamente o defeito relatado em 21/09/2026.
    """
    tasks = {cfg["task"] for cfg in celery_app.conf.beat_schedule.values()}
    despacha_boletim = "worker.tasks.licitacoes.dispatch_boletins" in tasks
    ingere = "worker.tasks.licitacoes.crawler_pncp" in tasks
    assert not (despacha_boletim and not ingere), (
        "beat despacha boletim mas nao agenda crawler_pncp: os boletins "
        "sairiam sobre uma base que ninguem atualiza"
    )


def test_dashboards_comerciais_tem_fonte_agendada() -> None:
    """Resultados e atas alimentam a Inteligencia comercial.

    Sem elas as tabelas ficam vazias e a tela aparece em branco, com
    cara de funcionalidade nao construida.
    """
    tasks = {cfg["task"] for cfg in celery_app.conf.beat_schedule.values()}
    assert "worker.tasks.licitacoes.ingest_resultados" in tasks
    assert "worker.tasks.licitacoes.ingest_atas" in tasks


def test_crawler_que_falha_em_tudo_nao_termina_verde() -> None:
    """Em 21/09/2026 o PNCP devolveu 503 nas 13 modalidades.

    A task voltou `total_fetched=0` com as 13 em `failed_modalidades` --
    e terminou com SUCESSO. No beat isso e uma execucao verde sobre uma
    base que nao andou: o mesmo defeito silencioso que deixou a tela
    parada em 22/04, agora com cara de job saudavel.
    """
    from worker.tasks.licitacoes import _falhar_se_nada_rodou

    tudo_falhou = {
        "total_fetched": 0,
        "failed_modalidades": [1, 2, 3],
    }
    with pytest.raises(RuntimeError, match="nenhuma modalidade"):
        _falhar_se_nada_rodou(tudo_falhou)


def test_falha_parcial_ou_dia_sem_publicacao_nao_e_erro() -> None:
    """Uma modalidade fora do ar nao invalida as outras doze, e um dia
    sem publicacao nenhuma (domingo) e resultado legitimo."""
    from worker.tasks.licitacoes import _falhar_se_nada_rodou

    _falhar_se_nada_rodou({"total_fetched": 40, "failed_modalidades": [7]})
    _falhar_se_nada_rodou({"total_fetched": 0, "failed_modalidades": []})
