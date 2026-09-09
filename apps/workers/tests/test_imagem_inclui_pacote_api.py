"""A imagem do worker PRECISA conter o pacote da API (`app`).

Os 6 modulos de task (`dp_sesmt`, `financeiro`, `fiscal`,
`licitacoes`, `manutencao`, `onedrive_diagnostico`) importam de `app.*`
e tratam `ImportError` devolvendo
`{"error": "API package not available in worker"}`.

Isso significa que uma imagem sem o pacote da API **nao quebra**: os 5
crons disparam no horario, cada task devolve o dict de erro e o
container segue de pe. Um deploy que parece saudavel e nao processa
nada -- foi o estado do `Dockerfile` ate 2026-09-09, no Railway e no
`docker-compose.prod.yml` (os dois buildavam com
`context: apps/workers`, que nao alcanca `apps/api`).

Nenhum teste de runtime pega isso: o venv do worker tambem nao tem o
pacote da API, entao os testes exercitam justamente o caminho do
ImportError. Por isso a verificacao aqui e da RECEITA da imagem (o
Dockerfile e o context do compose), nao do import.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_DOCKERFILE = _RAIZ / "apps" / "workers" / "Dockerfile"
_COMPOSES = (
    _RAIZ / "infra" / "docker-compose.yml",
    _RAIZ / "infra" / "docker-compose.prod.yml",
)


def _tasks_que_importam_a_api() -> list[str]:
    tasks = _RAIZ / "apps" / "workers" / "worker" / "tasks"
    return sorted(
        p.name
        for p in tasks.glob("*.py")
        if re.search(r"^\s*from app\.", p.read_text(), re.M)
    )


def test_ha_tasks_dependendo_do_pacote_da_api():
    """Guarda do proprio teste: se um dia nenhuma task importar `app`,
    esta suite inteira perde a razao de existir e deve ser removida --
    em vez de seguir passando sem proteger nada."""
    assert _tasks_que_importam_a_api()


def test_dockerfile_instala_o_pacote_da_api():
    conteudo = _DOCKERFILE.read_text()
    assert "apps/api" in conteudo, (
        "o Dockerfile do worker nao referencia apps/api -- as tasks "
        "cairiam no ImportError e os crons virariam no-op silencioso"
    )
    assert re.search(r"pip install[^\n]*apps/api", conteudo), (
        "o pacote da API precisa ser instalado na imagem do worker"
    )


def test_dockerfile_copia_o_codigo_da_api_antes_de_instalar():
    """Ordem importa: o editable install do setuptools congela o
    mapeamento de pacotes descobertos NO MOMENTO da instalacao. Se
    `apps/api/app` for copiado depois do `pip install -e`, o mapa sai
    vazio e `import app` falha em runtime mesmo com o codigo na imagem.
    """
    conteudo = _DOCKERFILE.read_text()
    copy_app = re.search(r"^COPY[^\n]*apps/api/app", conteudo, re.M)
    # `pip install` costuma vir em RUN multilinha (`\` + `&&`), entao
    # procuramos a instrucao em qualquer linha, nao so na que abre o RUN.
    install = re.search(r"pip install[^\n]*apps/api", conteudo)
    assert copy_app and install
    assert copy_app.start() < install.start(), (
        "COPY do codigo da API precisa vir ANTES do pip install -e"
    )


@pytest.mark.parametrize("compose", _COMPOSES, ids=lambda p: p.name)
def test_compose_builda_o_worker_a_partir_da_raiz(compose: Path):
    """`context: ../apps/workers` nao alcanca `apps/api`. O build do
    worker precisa da raiz do repo como contexto."""
    if not compose.exists():
        pytest.skip(f"{compose.name} ausente")
    conteudo = compose.read_text()
    bloco = re.search(
        r"^  workers:\n(?:(?:    .*)?\n)+?(?=^  \S|\Z)",
        conteudo,
        re.M,
    )
    assert bloco, f"servico `workers` nao encontrado em {compose.name}"
    trecho = bloco.group(0)
    assert "context: ../apps/workers" not in trecho, (
        f"{compose.name}: context aponta para apps/workers, que nao "
        "alcanca apps/api"
    )
    assert "dockerfile: apps/workers/Dockerfile" in trecho, (
        f"{compose.name}: com a raiz como context, o dockerfile precisa "
        "ser apontado explicitamente"
    )


def test_dockerignore_da_raiz_existe_e_corta_o_peso():
    """Com a raiz como contexto, o build enviaria ~767 MB (venvs +
    node_modules + .git) sem um .dockerignore na raiz -- o
    `apps/workers/.dockerignore` deixa de valer, porque o Docker le o
    arquivo na raiz do CONTEXTO."""
    ignore = _RAIZ / ".dockerignore"
    assert ignore.exists(), "falta .dockerignore na raiz do repo"
    conteudo = ignore.read_text()
    for padrao in (".venv", "node_modules", ".git"):
        assert padrao in conteudo, f"{padrao} deveria estar no .dockerignore"
