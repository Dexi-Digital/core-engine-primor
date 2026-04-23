FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]"

COPY . ./

CMD ["celery", "-A", "worker.main.celery_app", "worker", "--loglevel=info", "-Q", "default,dp_sesmt,manutencao,financeiro,licitacoes,ia"]
