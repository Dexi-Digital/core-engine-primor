# Imagem da API (FastAPI/uvicorn) e do job de migrations.
#
# ATENCAO ao contexto de build: e a RAIZ DO REPO, nao `apps/api`.
# Isso permite que o `railway.json` da RAIZ (caminho lido por padrao
# pelo Railway) aponte para ca sem precisar configurar "Root Directory"
# na UI servico a servico -- e alinha com o worker, que ja builda da
# raiz porque as tasks importam o pacote da API.
#
#   docker build -f apps/api/Dockerfile .              # da raiz
#   docker compose -f infra/docker-compose.prod.yml build api
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

# Migrations: o servico `release` (compose) e o `preDeployCommand`
# (Railway) rodam `alembic upgrade head` a partir deste WORKDIR.
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
