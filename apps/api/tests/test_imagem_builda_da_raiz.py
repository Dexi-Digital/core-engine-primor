"""Contratos das imagens Docker que dependem da RAIZ como contexto.

Duas formas de deploy convivem no repo:

- **VPS / producao**: `infra/docker-compose.prod.yml`, com api, workers
  e web separados (e o Celery rodando os 5 crons).
- **Demo no Railway**: `Dockerfile.allinone`, um container so com
  migrations + API + web. Nao tem worker -- os crons NAO rodam.

O `railway.json` da raiz aponta para a imagem unica porque e o caminho
que o Railway le por padrao, sem precisar configurar "Root Directory"
nem "Config File" na UI servico a servico.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_DOCKERFILE_API = _RAIZ / "apps" / "api" / "Dockerfile"
_DOCKERFILE_UNICA = _RAIZ / "Dockerfile.allinone"
_START = _RAIZ / "infra" / "start-allinone.sh"
_RAILWAY_RAIZ = _RAIZ / "railway.json"
_COMPOSES = (
    _RAIZ / "infra" / "docker-compose.yml",
    _RAIZ / "infra" / "docker-compose.prod.yml",
)


# --- imagem unica (deploy de demo) -----------------------------------


def test_railway_json_da_raiz_aponta_para_a_imagem_unica():
    assert _RAILWAY_RAIZ.exists(), (
        "sem railway.json na raiz o Railway cai no autodetect (Railpack), "
        "que nao sabe escolher entre apps/api, apps/web e apps/workers"
    )
    cfg = json.loads(_RAILWAY_RAIZ.read_text())
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["build"]["dockerfilePath"] == "Dockerfile.allinone"


def test_imagem_unica_traz_api_e_web():
    conteudo = _DOCKERFILE_UNICA.read_text()
    for origem in (
        "apps/api/pyproject.toml",
        "apps/api/app",
        "apps/api/alembic",
        "apps/api/alembic.ini",
        "apps/web/",
    ):
        assert origem in conteudo, f"{origem} precisa entrar na imagem"
    assert "/usr/local/bin/node" in conteudo, (
        "o Next standalone precisa do binario do node no runtime"
    )


def test_imagem_unica_aponta_o_front_para_a_api_local():
    """O front e server-side (cookie HTTP-only): quem chama a API e o
    Node, de DENTRO do container. Por isso 127.0.0.1 resolve e nao ha
    CORS -- o navegador so fala com rotas do proprio Next."""
    conteudo = _DOCKERFILE_UNICA.read_text()
    assert re.search(
        r"NEXT_PUBLIC_API_BASE_URL=http://127\.0\.0\.1:8000", conteudo
    ), "a base da API precisa ser inlined no build do Next"


def test_start_roda_migrations_antes_dos_servidores():
    """`relation "auth_users" does not exist` em producao veio de subir
    sem migration. Aqui elas rodam antes, e falha derruba o boot."""
    conteudo = _START.read_text()
    pos_alembic = conteudo.find("alembic upgrade head")
    pos_uvicorn = conteudo.find("uvicorn")
    pos_node = conteudo.find("node server.js")
    assert pos_alembic != -1 and pos_uvicorn != -1 and pos_node != -1
    assert pos_alembic < pos_uvicorn < pos_node
    assert "set -euo pipefail" in conteudo, (
        "sem `set -e` uma migration quebrada seguiria para o boot"
    )


def test_imagem_unica_carrega_o_seed_de_demo():
    """Sem `scripts/` na imagem, `python -m scripts.seed_dossie` nao
    existe no container -- e foi assim que o primeiro deploy foi ao ar
    com schema criado e ZERO linhas, com todas as telas vazias."""
    conteudo = _DOCKERFILE_UNICA.read_text()
    assert "apps/api/scripts" in conteudo


def test_seed_e_opt_in_e_roda_depois_das_migrations():
    """Seed depende das tabelas existirem, e nao pode disparar sozinho
    em ambiente que nao seja de demonstracao."""
    conteudo = _START.read_text()
    assert "SEED_DEMO" in conteudo
    pos_alembic = conteudo.find("alembic upgrade head")
    pos_seed = conteudo.find("scripts.seed_dossie")
    pos_uvicorn = conteudo.find("uvicorn")
    assert pos_alembic < pos_seed < pos_uvicorn


def test_start_evita_colisao_de_porta():
    """A API interna usa 8000. Se `PORT=8000` sobrar de uma config
    antiga, os dois processos disputariam a mesma porta."""
    conteudo = _START.read_text()
    assert "API_PORT" in conteudo and "WEB_PORT" in conteudo
    assert 'if [ "$WEB_PORT" = "$API_PORT" ]' in conteudo


def test_dockerignore_deixa_o_start_script_entrar():
    """`infra` e ignorado no contexto, mas o COPY do script precisa
    dele -- a negacao tem que vir DEPOIS da regra que exclui."""
    linhas = [
        ln.strip()
        for ln in (_RAIZ / ".dockerignore").read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert "infra" in linhas
    assert "!infra/start-allinone.sh" in linhas
    assert linhas.index("infra") < linhas.index("!infra/start-allinone.sh")


# --- imagem da API (compose / VPS) -----------------------------------


def test_dockerfile_da_api_copia_dos_caminhos_da_raiz():
    conteudo = _DOCKERFILE_API.read_text()
    assert "COPY . ./" not in conteudo, (
        "`COPY . ./` com contexto da raiz traria o repo inteiro "
        "(apps/web, infra, docs) para dentro da imagem da API"
    )
    for origem in (
        "apps/api/pyproject.toml",
        "apps/api/app",
        "apps/api/alembic",
        "apps/api/alembic.ini",
    ):
        assert origem in conteudo, f"{origem} precisa entrar na imagem"


def test_dockerfile_da_api_preserva_o_layout_interno():
    """`uvicorn app.main:app` e `alembic upgrade head` dependem de o
    pacote e o alembic.ini estarem na RAIZ do WORKDIR."""
    conteudo = _DOCKERFILE_API.read_text()
    assert re.search(r"^WORKDIR /app$", conteudo, re.M)
    assert re.search(r"^COPY apps/api/app \./app$", conteudo, re.M)
    assert re.search(r"^COPY apps/api/alembic\.ini \./", conteudo, re.M)


@pytest.mark.parametrize(
    "dockerfile", (_DOCKERFILE_API, _DOCKERFILE_UNICA), ids=lambda p: p.name
)
def test_codigo_copiado_antes_do_editable_install(dockerfile: Path):
    """O editable install do setuptools congela o mapa de pacotes
    descobertos no momento da instalacao; com `app/` ausente o mapa sai
    vazio e `import app` falha em runtime."""
    conteudo = dockerfile.read_text()
    copy_app = re.search(r"^COPY apps/api/app ", conteudo, re.M)
    install = re.search(r"pip install[^\n]*-e", conteudo)
    assert copy_app and install
    assert copy_app.start() < install.start()


@pytest.mark.parametrize("compose", _COMPOSES, ids=lambda p: p.name)
def test_composes_buildam_a_api_da_raiz(compose: Path):
    """O VPS continua com os servicos separados -- a imagem unica e so
    para a demo."""
    if not compose.exists():
        pytest.skip(f"{compose.name} ausente")
    conteudo = compose.read_text()
    assert "context: ../apps/api" not in conteudo
    assert conteudo.count("dockerfile: apps/api/Dockerfile") >= 1
