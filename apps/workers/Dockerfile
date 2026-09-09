# Imagem do worker Celery.
#
# ATENCAO ao contexto de build: e a RAIZ DO REPO, nao `apps/workers`.
# As tasks importam o pacote da API (`from app.*` em dp_sesmt,
# financeiro, fiscal, licitacoes, manutencao e onedrive_diagnostico),
# entao a imagem precisa alcancar `apps/api` -- inalcancavel com
# `context: ../apps/workers`.
#
# Isso nao era um erro barulhento: as tasks capturam o ImportError e
# devolvem {"error": "API package not available in worker"}, ou seja,
# os 5 crons disparavam no horario e nao processavam nada, com o
# container de pe. Ver tests/test_imagem_inclui_pacote_api.py.
#
#   docker build -f apps/workers/Dockerfile .            # da raiz
#   docker compose -f infra/docker-compose.prod.yml build workers
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Codigo ANTES do install: o editable install do setuptools congela o
# mapeamento de pacotes descobertos no momento da instalacao. Instalar
# com `apps/api/app` ausente geraria um mapa vazio, e `import app`
# falharia em runtime mesmo com o codigo presente na imagem.
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
COPY apps/api/app ./apps/api/app
COPY apps/workers/pyproject.toml ./apps/workers/pyproject.toml
COPY apps/workers/worker ./apps/workers/worker

RUN pip install --upgrade pip \
    && pip install -e ./apps/api \
    && pip install -e ./apps/workers

# O `-A worker.main.celery_app` resolve a partir do pacote instalado,
# nao do CWD -- por isso o WORKDIR pode seguir em /app.
#
# `-B` = beat embutido (Option A roda 1 instancia worker+beat junta;
# se voce escalar pra >1 worker, separe num processo proprio para
# evitar duplicar cron jobs). `-s /tmp/celerybeat-schedule` mantem o
# schedule file em tmpfs (ok porque o conteudo e regenerado a cada
# boot a partir do `celery_app.conf.beat_schedule`).
CMD ["celery", "-A", "worker.main.celery_app", "worker", "-B", "-s", "/tmp/celerybeat-schedule", "--loglevel=info", "-Q", "default,dp_sesmt,manutencao,financeiro,licitacoes,ia"]
