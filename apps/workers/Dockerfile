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

# `-B` = beat embutido (Option A roda 1 instancia worker+beat junta;
# se voce escalar pra >1 worker, separe num processo proprio para
# evitar duplicar cron jobs). `-s /tmp/celerybeat-schedule` mantem o
# schedule file em tmpfs (ok porque o conteudo e regenerado a cada
# boot a partir do `celery_app.conf.beat_schedule`).
CMD ["celery", "-A", "worker.main.celery_app", "worker", "-B", "-s", "/tmp/celerybeat-schedule", "--loglevel=info", "-Q", "default,dp_sesmt,manutencao,financeiro,licitacoes,ia"]
