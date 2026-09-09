"""A imagem da API tem que buildar com a RAIZ do repo como contexto.

Motivo prático: sem isso, publicar exige configurar "Root Directory" e
"Railway Config File" na UI do Railway serviço a serviço. Com o
`railway.json` na raiz (caminho que o Railway lê por padrão) e um
Dockerfile que aceita contexto da raiz, o deploy funciona sem nenhuma
configuração manual -- e fica igual ao worker, que já builda assim
porque precisa alcançar `apps/api`.

O layout DENTRO da imagem continua o mesmo de antes (`/app/app`,
`/app/alembic.ini`): é o que mantém `uvicorn app.main:app` e
`alembic upgrade head` funcionando sem mudar comando nenhum.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_DOCKERFILE = _RAIZ / "apps" / "api" / "Dockerfile"
_RAILWAY_RAIZ = _RAIZ / "railway.json"
_COMPOSES = (
    _RAIZ / "infra" / "docker-compose.yml",
    _RAIZ / "infra" / "docker-compose.prod.yml",
)


def test_railway_json_na_raiz_aponta_para_o_dockerfile_da_api():
    assert _RAILWAY_RAIZ.exists(), (
        "sem railway.json na raiz o Railway cai no autodetect (Railpack), "
        "que nao sabe escolher entre apps/api, apps/web e apps/workers"
    )
    cfg = json.loads(_RAILWAY_RAIZ.read_text())
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["build"]["dockerfilePath"] == "apps/api/Dockerfile"


def test_railway_json_da_raiz_roda_as_migrations_antes_de_subir():
    """`preDeployCommand` aborta o deploy se a migration falhar, sem
    virar o trafego para a versao nova."""
    cfg = json.loads(_RAILWAY_RAIZ.read_text())
    assert "alembic upgrade head" in cfg["deploy"]["preDeployCommand"]


def test_dockerfile_da_api_copia_dos_caminhos_da_raiz():
    conteudo = _DOCKERFILE.read_text()
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


def test_dockerfile_preserva_o_layout_interno():
    """`uvicorn app.main:app` e `alembic upgrade head` dependem de o
    pacote e o alembic.ini estarem na RAIZ do WORKDIR, como antes."""
    conteudo = _DOCKERFILE.read_text()
    assert re.search(r"^WORKDIR /app$", conteudo, re.M)
    assert re.search(r"^COPY apps/api/app \./app$", conteudo, re.M)
    assert re.search(r"^COPY apps/api/alembic\.ini \./", conteudo, re.M)


def test_codigo_copiado_antes_do_editable_install():
    """O editable install do setuptools congela o mapa de pacotes
    descobertos no momento da instalacao; com `app/` ausente o mapa sai
    vazio e `import app` falha em runtime."""
    conteudo = _DOCKERFILE.read_text()
    copy_app = re.search(r"^COPY apps/api/app ", conteudo, re.M)
    install = re.search(r"pip install[^\n]*-e", conteudo)
    assert copy_app and install
    assert copy_app.start() < install.start()


@pytest.mark.parametrize("compose", _COMPOSES, ids=lambda p: p.name)
def test_composes_buildam_a_api_da_raiz(compose: Path):
    """O VPS precisa construir a MESMA imagem que o Railway constroi --
    e a razao de usar Docker nos dois lugares."""
    if not compose.exists():
        pytest.skip(f"{compose.name} ausente")
    conteudo = compose.read_text()
    assert "context: ../apps/api" not in conteudo, (
        f"{compose.name}: a API deve buildar da raiz (`context: ..`)"
    )
    assert conteudo.count("dockerfile: apps/api/Dockerfile") >= 1
