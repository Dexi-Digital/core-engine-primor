# Imagem da API (FastAPI/uvicorn) e do job de migrations.
#
# ATENCAO ao contexto de build: e a RAIZ DO REPO, nao `apps/api` --
# alinha com o worker, que ja builda da raiz porque as tasks importam
# o pacote da API.
#
#   docker build -f apps/api/Dockerfile .              # da raiz
#   docker compose -f infra/docker-compose.prod.yml build api
#
# NAO e esta imagem que o `railway.json` da raiz usa: ele builda o
# `Dockerfile.allinone` (migrations + API + web num container so). Esta
# e a API ISOLADA -- usada pelo compose de producao e por um servico
# Railway configurado a mao. O CMD abaixo sobe SO o uvicorn: quem roda
# `alembic upgrade head` e o servico `release` do compose ou, no
# Railway, um pre-deploy command que voce precisa configurar no painel
# (nenhum arquivo versionado faz isso). Sem ele, nenhuma migration roda.
# Ver docs/deployment-railway.md.
#
# O layout DENTRO da imagem e o mesmo de sempre (`/app/app`,
# `/app/alembic.ini`), entao `uvicorn app.main:app` e
# `alembic upgrade head` seguem funcionando sem mudar comando.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Codigo ANTES do install: o editable install do setuptools congela o
# mapeamento de pacotes descobertos no momento da instalacao. Instalar
# com `app/` ausente geraria um mapa vazio e `import app` falharia em
# runtime mesmo com o codigo presente na imagem.
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app

RUN pip install --upgrade pip && pip install -e ".[dev]"

# Migrations: o servico `release` (compose) -- ou um pre-deploy command
# configurado no painel do Railway -- roda `alembic upgrade head` a
# partir deste WORKDIR. O CMD desta imagem nao roda.
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
